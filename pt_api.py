import struct
def gen_xor_delta(xor_value: int, mul: int, negative: bool) -> int:
    for i in range(256):
        if ((i * mul) & 0xff) == xor_value:
            if negative:
                return (256 - i) & 0xff
            else:
                return i
    return 0

def unxor_session(file_path: str) -> bytearray:
    try:
        with open(file_path, "rb") as f:
            data = bytearray(f.read())
    except IOError as e:
        raise Exception(f"Cannot open file {file_path}: {e}")
        
    if len(data) < 0x14:
        raise Exception("File is too small to be a valid Pro Tools session.")
        
    xor_type = data[0x12]
    xor_value = data[0x13]
    
    if xor_type == 0x01:
        xor_delta = gen_xor_delta(xor_value, 53, False)
    elif xor_type == 0x05:
        xor_delta = gen_xor_delta(xor_value, 11, True)
    else:
        raise Exception(f"Unknown encryption type (xor_type: 0x{xor_type:02x})")
        
    xxor = bytearray(256)
    for i in range(256):
        xxor[i] = (i * xor_delta) & 0xff
        
    for i in range(0x14, len(data)):
        if xor_type == 0x01:
            xor_index = i & 0xff
        else:
            xor_index = (i >> 12) & 0xff
        data[i] ^= xxor[xor_index]
        
    return data

def xor_session(data: bytearray, out_path: str):
    xor_type = data[0x12]
    xor_value = data[0x13]
    
    if xor_type == 0x01:
        xor_delta = gen_xor_delta(xor_value, 53, False)
    elif xor_type == 0x05:
        xor_delta = gen_xor_delta(xor_value, 11, True)
    else:
        raise Exception(f"Unknown encryption type")
        
    xxor = bytearray(256)
    for i in range(256):
        xxor[i] = (i * xor_delta) & 0xff
        
    for i in range(0x14, len(data)):
        if xor_type == 0x01:
            xor_index = i & 0xff
        else:
            xor_index = (i >> 12) & 0xff
        data[i] ^= xxor[xor_index]
        
    with open(out_path, "wb") as f:
        f.write(data)

class TimecodeEngine:
    def __init__(self, sample_rate, frame_rate_enum):
        self.sample_rate = sample_rate
        self.frame_rate_enum = frame_rate_enum

    def get_frame_rate(self):
        # Enum mapping based on block 0x204d payload offset 2
        # 0x01 = 24 fps
        # 0x09 = 23.976 fps
        # 0x05 = 29.97 DF fps
        if self.frame_rate_enum == 0x01:
            return 24.0, False # fps, is_drop_frame
        elif self.frame_rate_enum == 0x09:
            return 24000 / 1001, False
        elif self.frame_rate_enum == 0x05:
            return 30000 / 1001, True
        else:
            # default fallback
            return 24.0, False

    def samples_to_timecode(self, samples):
        actual_fps, is_df = self.get_frame_rate()
        
        if self.frame_rate_enum == 0x09: # 23.976
            nominal_fps = 24
        elif self.frame_rate_enum == 0x05: # 29.97 DF
            nominal_fps = 30
        else:
            nominal_fps = int(actual_fps)
            
        total_seconds = samples / self.sample_rate
        
        if not is_df:
            total_frames = int(round(total_seconds * actual_fps))
            ff = total_frames % nominal_fps
            ss = (total_frames // nominal_fps) % 60
            mm = (total_frames // (nominal_fps * 60)) % 60
            hh = total_frames // (nominal_fps * 3600)
        else:
            # Drop frame logic (29.97)
            frames = int(round(total_seconds * actual_fps))
            d = frames // 17982
            m = frames % 17982
            if m >= 2:
                frames += 18 * d + 2 * ((m - 2) // 1798)
                
            ff = frames % 30
            ss = (frames // 30) % 60
            mm = (frames // 1800) % 60
            hh = frames // 108000
            
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    def timecode_to_samples(self, hh, mm, ss, ff):
        actual_fps, is_df = self.get_frame_rate()

        if self.frame_rate_enum == 0x09: # 23.976
            nominal_fps = 24
        elif self.frame_rate_enum == 0x05: # 29.97 DF
            nominal_fps = 30
        else:
            nominal_fps = int(actual_fps)

        if not is_df:
            total_frames = (hh * 3600 + mm * 60 + ss) * nominal_fps + ff
            total_seconds = total_frames / actual_fps
            return int(round(total_seconds * self.sample_rate))
        else:
            # Drop frame logic
            # For 29.97 DF, frame rate is exactly 30000/1001
            # 2 frames are dropped every minute, except every 10th minute
            total_minutes = hh * 60 + mm

            # The nominal frame count if it were exactly 30 fps
            nominal_frames = (total_minutes * 60 + ss) * 30 + ff

            # Subtract dropped frames
            dropped = 2 * total_minutes - 2 * (total_minutes // 10)

            actual_frames = nominal_frames - dropped

            total_seconds = actual_frames / (30000 / 1001)
            return int(round(total_seconds * self.sample_rate))

ZMARK = 0x5a

def u_endian_read2(buf, offset, is_bigendian):
    fmt = ">H" if is_bigendian else "<H"
    return struct.unpack_from(fmt, buf, offset)[0]

def u_endian_read4(buf, offset, is_bigendian):
    fmt = ">I" if is_bigendian else "<I"
    return struct.unpack_from(fmt, buf, offset)[0]

class PTBlock:
    def __init__(self, block_type, content_type, original_size=0):
        self.block_type = block_type
        self.content_type = content_type
        self.original_size = original_size
        self.items = [] # list of bytearray or PTBlock
        self.original_offset = 0

    def to_bytes(self, is_bigendian=False, base_offset=0):
        import struct
        payload = bytearray()
        mapping = {}

        # Calculate start of payload
        current_offset = base_offset + 9

        for item in self.items:
            if isinstance(item, PTBlock):
                child_bytes, child_mapping = item.to_bytes(is_bigendian, current_offset)
                payload.extend(child_bytes)
                mapping.update(child_mapping)
                current_offset += len(child_bytes)
            else:
                payload.extend(item)
                current_offset += len(item)

        fmt_type = ">H" if is_bigendian else "<H"
        fmt_size = ">I" if is_bigendian else "<I"
        fmt_ctype = ">H" if is_bigendian else "<H"

        if self.original_size == 0 and len(payload) == 0:
            size = 0
            header = bytearray([0x5a])
            header.extend(struct.pack(fmt_type, self.block_type))
            header.extend(struct.pack(fmt_size, size))
        else:
            size = len(payload) + 2
            header = bytearray([0x5a])
            header.extend(struct.pack(fmt_type, self.block_type))
            header.extend(struct.pack(fmt_size, size))
            header.extend(struct.pack(fmt_ctype, self.content_type))

        if self.original_offset > 0:
            mapping[self.original_offset] = base_offset

        return header + payload, mapping

    def serialize(self, is_bigendian):
        payload = bytearray()
        for item in self.items:
            if isinstance(item, PTBlock):
                payload.extend(item.serialize(is_bigendian))
            else:
                payload.extend(item)

        size = len(payload) + 2 # +2 for content_type

        fmt_type = ">H" if is_bigendian else "<H"
        fmt_size = ">I" if is_bigendian else "<I"
        fmt_ctype = ">H" if is_bigendian else "<H"

        header = bytearray()
        header.append(ZMARK)
        header.extend(struct.pack(fmt_type, self.block_type))
        header.extend(struct.pack(fmt_size, size))
        header.extend(struct.pack(fmt_ctype, self.content_type))

        return header + payload

    def get_all_blocks(self, content_type=None):
        res = []
        if content_type is None or self.content_type == content_type:
            res.append(self)
        for item in self.items:
            if isinstance(item, PTBlock):
                res.extend(item.get_all_blocks(content_type))
        return res

class ProToolsSession:
    def __init__(self, file_path):
        self.file_path = file_path
        print(f"Loading {file_path}...")
        self.data = unxor_session(file_path)

        if len(self.data) < 0x14:
            raise Exception("File too small")

        self.is_bigendian = bool(self.data[0x11])

        # Parse root blocks
        self.root_items = []

        pos = 0x14
        current_bytes = bytearray()

        while pos < len(self.data):
            if self.data[pos] == ZMARK:
                block, jump = self._parse_block(pos, len(self.data))
                if block:
                    if current_bytes:
                        self.root_items.append(current_bytes)
                        current_bytes = bytearray()
                    self.root_items.append(block)
                    pos += jump
                    continue
            current_bytes.append(self.data[pos])
            pos += 1

        if current_bytes:
            self.root_items.append(current_bytes)

        self._parse_session_metadata()

        # Tracks original_offset values of blocks that were physically
        # removed from the tree (e.g. by delete_clip_group()). These no
        # longer appear in global_mapping at save() time, so their stale
        # 0x0002 records must be purged explicitly instead of left dangling.
        self._removed_offsets = []

    def _collect_offsets_recursive(self, node, out):
        """Recursively collect original_offset (>0) of node and all its
        PTBlock descendants. Used before removing a block from the tree so
        we know which 0x0002 records must be purged at save() time."""
        if isinstance(node, PTBlock):
            if node.original_offset and node.original_offset > 0:
                out.append(node.original_offset)
            for child in node.items:
                self._collect_offsets_recursive(child, out)

    def get_tracks(self):
        """Returns a list of all track names in the session."""
        tracks = []
        b1054 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x1054), None)
        if b1054:
            for track in [b for b in b1054.items if isinstance(b, PTBlock) and b.content_type == 0x1052]:
                if len(track.items) > 0 and isinstance(track.items[0], (bytes, bytearray)):
                    hdr = track.items[0]
                    nlen = struct.unpack_from("<I", hdr, 0)[0]
                    if 4 + nlen <= len(hdr):
                        tname = hdr[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                        tracks.append(tname)
        return tracks

    def get_markers(self):
        """Returns a list of dictionaries describing session markers.
        Example: [{'index': 1, 'name': 'Intro', 'timecode': '00:00:10:00'}]
        """
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        markers = []
        marker_indices = [i for i, x in enumerate(self.root_items) if isinstance(x, PTBlock) and x.content_type == 0x2030]
        for idx in marker_indices:
            container = self.root_items[idx]
            for child in container.items:
                if isinstance(child, PTBlock) and child.content_type == 0x2077:
                    payload = child.items[0]
                    m_idx = struct.unpack_from("<H", payload, 0)[0]
                    nlen = struct.unpack_from("<I", payload, 6)[0]
                    name = payload[10:10+nlen].decode('ascii', 'ignore').strip('\x00')
                    tc_samples = struct.unpack_from("<q", payload, 10+nlen)[0]
                    markers.append({'index': m_idx, 'name': name, 'timecode': engine.samples_to_timecode(tc_samples)})
        return markers

    def get_clips(self):
        """Returns a list of dictionaries describing available clips and clip groups.
        Example: [{'name': 'Audio.wav', 'type': 'parent', 'length': '00:01:00:00', 'src_offset': '00:00:00:00'}]
        """
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        clips = []
        b262a = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262a), None)
        b262c = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262c), None)
        
        # 1. Parse normal clips
        if b262a:
            for child in getattr(b262a, 'items', []):
                if isinstance(child, PTBlock) and child.content_type == 0x2629:
                    b2628 = next((c for c in child.items if isinstance(c, PTBlock) and c.content_type == 0x2628), None)
                    if b2628:
                        payload = getattr(b2628, 'items', [b""])[0]
                        if not isinstance(payload, (bytes, bytearray)) or len(payload) < 4:
                            continue
                        nlen = struct.unpack_from("<I", payload, 0)[0]
                        if 4 + nlen > len(payload):
                            continue
                        name = payload[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                        flags = struct.unpack_from("<H", payload, 4+nlen)[0]
                        ctype = "parent" if flags == 0x0000 else "virtual"
                        if flags in (0x0000, 0x0001):
                            length = struct.unpack_from("<I", payload, 4+nlen+5)[0]
                            src_offset = 0
                        elif flags == 0x3001:  # 01 30 -> 0x3001
                            offset_bytes = payload[4+nlen+5 : 4+nlen+8]
                            len_bytes = payload[4+nlen+8 : 4+nlen+11]
                            src_offset = offset_bytes[0] | (offset_bytes[1]<<8) | (offset_bytes[2]<<16)
                            length = len_bytes[0] | (len_bytes[1]<<8) | (len_bytes[2]<<16)
                        else:
                            length = 0
                            src_offset = 0
                        clips.append({
                            'name': name,
                            'type': ctype,
                            'length': engine.samples_to_timecode(length),
                            'src_offset': engine.samples_to_timecode(src_offset)
                        })
                        
        # 2. Parse clip groups
        if b262c:
            for child in getattr(b262c, 'items', []):
                if isinstance(child, PTBlock) and child.content_type == 0x262b:
                    b2628 = next((c for c in child.items if isinstance(c, PTBlock) and c.content_type == 0x2628), None)
                    if b2628:
                        payload = getattr(b2628, 'items', [b""])[0]
                        if not isinstance(payload, (bytes, bytearray)) or len(payload) < 4:
                            continue
                        nlen = struct.unpack_from("<I", payload, 0)[0]
                        if 4 + nlen > len(payload):
                            continue
                        name = payload[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                        ctype = "group"
                        length = 0
                        if 4+nlen+9 <= len(payload):
                            length = struct.unpack_from("<I", payload, 4+nlen+5)[0]
                        clips.append({
                            'name': name,
                            'type': ctype,
                            'length': engine.samples_to_timecode(length),
                            'src_offset': engine.samples_to_timecode(0)
                        })
        return clips

    def delete_clip_group(self, group_name):
        """Removes a clip group from the session (both from the clip list and the timeline)."""
        import struct
        
        # 1. Find the 0x262b block in the clip group list (0x262c)
        b262c = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262c), None)
        if not b262c:
            return 0
            
        group_id = None
        group_block = None
        
        # The first item in b262c is the count payload.
        clip_index = 0
        for i, child in enumerate(b262c.items[1:], start=1):
            if isinstance(child, PTBlock) and child.content_type == 0x262b:
                b2628 = next((x for x in child.items if isinstance(x, PTBlock) and x.content_type == 0x2628), None)
                if b2628:
                    payload = getattr(b2628, 'items', [b""])[0]
                    if isinstance(payload, (bytes, bytearray)) and len(payload) >= 4:
                        nlen = struct.unpack_from("<I", payload, 0)[0]
                        name = payload[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                        if name == group_name:
                            group_id = clip_index
                            group_block = child
                            break
                clip_index += 1
                
        if group_block is None:
            raise ValueError(f"Clip group '{group_name}' not found.")

        # CRITICAL: per architecture.md, we must NEVER pop() a block that
        # has a live pointer in the 0x0002 table without also purging that
        # pointer's record — otherwise save() leaves a dangling pointer and
        # Pro Tools throws "Magic ID does not match". Since a deletion has
        # no natural replacement to mutate-in-place (unlike split_clip),
        # we instead track every original_offset being removed here, and
        # save() strips the matching 0x0002 records for them (see
        # _purge_0002_records()).
        self._collect_offsets_recursive(group_block, self._removed_offsets)

        # 2. Remove from 0x262c (Clip group list)
        b262c.items.remove(group_block)
        
        # Decrement count in 0x262c payload
        count_pl = bytearray(b262c.items[0])
        old_count = struct.unpack_from("<I", count_pl, 0)[0]
        struct.pack_into("<I", count_pl, 0, old_count - 1)
        b262c.items[0] = count_pl
        
        # 3. Remove all events referencing this group_id from the timeline (0x1054 -> 0x1052 -> 0x1050)
        deleted_count = 0
        b1054 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x1054), None)
        if b1054:
            for track in [b for b in b1054.items if isinstance(b, PTBlock) and b.content_type == 0x1052]:
                events_to_remove = []
                for ev in track.items:
                    if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                        b104f = next((x for x in ev.items if isinstance(x, PTBlock) and x.content_type == 0x104f), None)
                        if b104f and isinstance(b104f.items[0], (bytes, bytearray)):
                            cid = struct.unpack_from("<I", b104f.items[0], 2)[0]
                            if cid == group_id:
                                events_to_remove.append(ev)
                
                for ev in events_to_remove:
                    self._collect_offsets_recursive(ev, self._removed_offsets)
                    track.items.remove(ev)
                    deleted_count += 1
                    
        return deleted_count

    def _parse_block(self, pos, max_limit):
        if pos + 9 > max_limit:
            return None, 0

        block_type = u_endian_read2(self.data, pos + 1, self.is_bigendian)
        block_size = u_endian_read4(self.data, pos + 3, self.is_bigendian)

        if pos + 7 + block_size > max_limit:
            return None, 0

        if (block_type & 0xff00) != 0:
            return None, 0

        content_type = 0
        if block_size >= 2:
            content_type = u_endian_read2(self.data, pos + 7, self.is_bigendian)
            payload_start = pos + 9
        else:
            payload_start = pos + 7

        block = PTBlock(block_type, content_type, block_size)
        block.original_offset = pos

        payload_end = pos + 7 + block_size

        i = payload_start
        current_bytes = bytearray()

        while i < payload_end:
            if self.data[i] == ZMARK:
                child, jump = self._parse_block(i, payload_end)
                if child:
                    if current_bytes:
                        block.items.append(current_bytes)
                        current_bytes = bytearray()
                    block.items.append(child)
                    i += jump
                    continue
            current_bytes.append(self.data[i])
            i += 1

        if current_bytes:
            block.items.append(current_bytes)

        return block, 7 + block_size

    def add_marker(self, name, tc_samples, index=1):
        from binascii import unhexlify
        import struct
        import os
        template_bytes = unhexlify("5a05003c0100003020010000005a12002701000077200100030900000c0000004d41524b45525f414c504841a059650a00000000a059650a00000000000000000000f0bfffffffffffffffffffffffffffbfffffffffffffffbf00000000000000400000000000000040ffffffff01000000000100000000000000000000000000000000ffffffff000000000000000000000000ffffffff0000000000000000000000001500010000005a03000a0000000625ffffffff00000000000000000000000000000000000000000000000000ffffb2056497c372490ba13368d4d04e64cfffffffffffffffffffffffff5a010009000000264801003f009a00ff5a0100090000002648000000000000005a01001e000000274800000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000")

        temp_sess = ProToolsSession.__new__(ProToolsSession)
        temp_sess.data = template_bytes
        temp_sess.is_bigendian = False
        block, _ = temp_sess._parse_block(0, len(template_bytes))

        block.items[0] = struct.pack("<I", index)

        b2077 = block.items[1]

        new_name_bytes = name.encode('ascii')
        new_len = len(new_name_bytes)

        header = bytearray(struct.pack("<H", index))
        header.extend([0x03, 0x09, 0x00, 0x00])
        header.extend(struct.pack("<I", new_len))
        header.extend(new_name_bytes)
        header.extend(struct.pack("<q", tc_samples))
        header.extend(struct.pack("<q", tc_samples))

        rest = unhexlify("000000000000f0bfffffffffffffffffffffffffffbfffffffffffffffbf00000000000000400000000000000040ffffffff01000000000100000000000000000000000000000000ffffffff000000000000000000000000ffffffff000000000000000000000000150001000000")
        header.extend(rest)
        b2077.items[0] = header

        import os
        # The second payload part contains a 16-byte UUID starting at offset 20
        payload2 = bytearray(b2077.items[2])
        random_uuid = os.urandom(16)
        payload2[20:36] = random_uuid
        b2077.items[2] = payload2

        # Find existing root-level 0x2030 blocks
        marker_indices = [i for i, x in enumerate(self.root_items) if isinstance(x, PTBlock) and x.content_type == 0x2030]

        # Note: block here is a 0x2030 block because we used the template which wraps the marker in a 0x2030.
        # But we actually just want the 0x2077 block!
        marker_02077 = block.items[1]

        if marker_indices:
            # Get the root 0x2030 container
            container = self.root_items[marker_indices[0]]

            # If the container is empty (size 12, meaning payload is 0s)
            if len(container.items) == 1 and len(container.items[0]) == 12:
                container.items = [bytearray([1, 0, 0, 0]), marker_02077, bytearray(8)]
            else:
                # Get current count
                count = struct.unpack("<I", container.items[0])[0]
                container.items[0] = struct.pack("<I", count + 1)
                # Insert the marker right before the final 8 bytes of padding
                container.items.insert(-1, marker_02077)
        else:
            # Fallback if no 0x2030 block is found, we create the container
            container = PTBlock(0x05, 0x2030, 0)
            container.items = [bytearray([1, 0, 0, 0]), marker_02077, bytearray(8)]
            self.root_items.append(container)

    def _parse_session_metadata(self):
        self.sample_rate = 48000
        self.frame_rate_enum = 0x01 # 24 by default

        import struct
        for item in self.root_items:
            if isinstance(item, PTBlock):
                if item.content_type == 0x1028:
                    if len(item.items) > 0 and isinstance(item.items[0], bytearray) and len(item.items[0]) >= 6:
                        # sample rate is a 4-byte LE uint at offset 2 in the payload
                        fmt = ">I" if self.is_bigendian else "<I"
                        self.sample_rate = struct.unpack_from(fmt, item.items[0], 2)[0]
                elif item.content_type == 0x204d:
                    if len(item.items) > 0 and isinstance(item.items[0], bytearray) and len(item.items[0]) > 0:
                        # framerate enum is 1 byte at offset 0 in the payload
                        self.frame_rate_enum = item.items[0][0]

    def mute_clip(self, clip_name, mute=True):
        import struct
        # 1. Find Region ID from clip name
        region_id = None

        b262a = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262a), None)
        if not b262a:
            raise ValueError("No clips found in session.")
            
        region_id = -1
        current_idx = 0
        for child in b262a.items:
            if isinstance(child, PTBlock) and child.content_type == 0x2629:
                b2628 = next((c for c in child.items if isinstance(c, PTBlock) and c.content_type == 0x2628), None)
                if b2628:
                    payload = getattr(b2628, 'items', [b""])[0]
                    if isinstance(payload, (bytes, bytearray)) and len(payload) >= 4:
                        nlen = struct.unpack_from("<I", payload, 0)[0]
                        if 4 + nlen <= len(payload):
                            name = payload[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                            if name == clip_name:
                                region_id = current_idx
                                break
                current_idx += 1

        if region_id == -1:
            raise ValueError(f"Clip '{clip_name}' not found in session.")

        def find_blocks(item, ctype):
            found = []
            if isinstance(item, PTBlock):
                if item.content_type == ctype:
                    found.append(item)
                for child in item.items:
                    found.extend(find_blocks(child, ctype))
            return found

        # 2. Find placement in 0x1050 -> 0x104f
        all_104f = []
        for r in self.root_items:
            all_104f.extend(find_blocks(r, 0x104f))

        muted_count = 0
        for b104f in all_104f:
            if len(b104f.items) > 0 and isinstance(b104f.items[0], bytearray) and len(b104f.items[0]) >= 7:
                payload = b104f.items[0]
                placed_id = struct.unpack_from("<I", payload, 2)[0]
                if placed_id == region_id:
                    # Mute flag is at offset 0
                    payload[0] = 0x01 if mute else 0x00
                    muted_count += 1

        return muted_count

    def move_clip(self, clip_name, hh, mm, ss, ff):
        import struct
        # 1. Calculate target samples using TimecodeEngine
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        target_samples = engine.timecode_to_samples(hh, mm, ss, ff)

        # 2. Find Region ID
        region_id = -1
        b262a = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262a), None)
        if not b262a:
            raise ValueError("No clips found in session.")
            
        current_idx = 0
        for child in b262a.items:
            if isinstance(child, PTBlock) and child.content_type == 0x2629:
                b2628 = next((c for c in child.items if isinstance(c, PTBlock) and c.content_type == 0x2628), None)
                if b2628:
                    payload = getattr(b2628, 'items', [b""])[0]
                    if isinstance(payload, (bytes, bytearray)) and len(payload) >= 4:
                        nlen = struct.unpack_from("<I", payload, 0)[0]
                        if 4 + nlen <= len(payload):
                            name = payload[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                            if name == clip_name:
                                region_id = current_idx
                                break
                current_idx += 1

        if region_id == -1:
            raise ValueError(f"Clip '{clip_name}' not found in session.")

        # 3. Update timestamp in 0x104f
        moved_count = 0
        def find_blocks(item, ctype):
            found = []
            if isinstance(item, PTBlock):
                if item.content_type == ctype:
                    found.append(item)
                for child in item.items:
                    found.extend(find_blocks(child, ctype))
            return found
            
        for b104f in sum((find_blocks(r, 0x104f) for r in self.root_items), []):
            if len(b104f.items) > 0 and isinstance(b104f.items[0], bytearray) and len(b104f.items[0]) >= 15:
                payload = b104f.items[0]
                placed_id = struct.unpack_from("<I", payload, 2)[0]
                if placed_id == region_id:
                    # Timestamp is 8 bytes at offset 7
                    struct.pack_into("<q", payload, 7, target_samples)
                    moved_count += 1

        return moved_count

    def rename_clip(self, old_name, new_name):
        import struct
        renamed_count = 0
        b262a = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262a), None)
        if b262a:
            for child in b262a.items:
                if isinstance(child, PTBlock) and child.content_type == 0x2629:
                    b2628 = next((c for c in child.items if isinstance(c, PTBlock) and c.content_type == 0x2628), None)
                    if b2628:
                        payload = getattr(b2628, 'items', [b""])[0]
                        if isinstance(payload, (bytes, bytearray)) and len(payload) >= 4:
                            nlen = struct.unpack_from("<I", payload, 0)[0]
                            if 4 + nlen <= len(payload):
                                name = payload[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                                if name == old_name:
                                    name_bytes = new_name.encode('ascii') + b'\x00'
                                    new_len = len(name_bytes)
                                    new_payload = bytearray(4 + new_len + (len(payload) - (4 + nlen)))
                                    struct.pack_into("<I", new_payload, 0, new_len)
                                    new_payload[4:4+new_len] = name_bytes
                                    new_payload[4+new_len:] = payload[4+nlen:]
                                    b2628.items[0] = new_payload
                                    renamed_count += 1
        return renamed_count

    def duplicate_clip(self, clip_name, hh, mm, ss, ff, mute=False):
        """Duplicates an existing clip on the timeline and places it at the specified timecode."""
        import copy
        import struct
        
        target_event = None
        target_b1052 = None
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        new_samples = engine.timecode_to_samples(hh, mm, ss, ff)
        
        b262a = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262a), None)
        if not b262a:
            raise ValueError("No clips found in session.")
            
        clip_id = -1
        current_idx = 0
        for child in b262a.items:
            if isinstance(child, PTBlock) and child.content_type == 0x2629:
                b2628 = next((c for c in child.items if isinstance(c, PTBlock) and c.content_type == 0x2628), None)
                if b2628:
                    payload = getattr(b2628, 'items', [b""])[0]
                    if isinstance(payload, (bytes, bytearray)) and len(payload) >= 4:
                        nlen = struct.unpack_from("<I", payload, 0)[0]
                        if 4 + nlen <= len(payload):
                            name = payload[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                            if name == clip_name:
                                clip_id = current_idx
                                break
                current_idx += 1
                
        if clip_id == -1:
            raise ValueError(f"Clip '{clip_name}' not found.")
            
        # Find the event for this clip_id
        b1054 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x1054), None)
        if not b1054:
            raise ValueError("No track map (0x1054) found.")
            
        for child in b1054.items:
            if isinstance(child, PTBlock) and child.content_type == 0x1052:
                for ev in child.items:
                    if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                        b104f = next((c for c in ev.items if isinstance(c, PTBlock) and c.content_type == 0x104f), None)
                        if b104f:
                            payload = bytearray(b104f.items[0])
                            if len(payload) >= 6:
                                ev_clip_id = struct.unpack_from("<I", payload, 2)[0]
                                if ev_clip_id == clip_id:
                                    target_event = ev
                                    target_b1052 = child
                                    break
                if target_event: break
            if target_event: break
            
        if not target_event or not target_b1052:
            raise ValueError(f"Could not find an event for clip '{clip_name}' on the timeline.")
            
        # Duplicate the event
        new_event = copy.deepcopy(target_event)
        # Clear the original_offset so it is treated as a new block (won't inherit 0002 pointer)
        self._wipe_offsets_recursive(new_event)
        
        b104f = next(c for c in new_event.items if isinstance(c, PTBlock) and c.content_type == 0x104f)
        payload = bytearray(b104f.items[0])
        
        # Mute flag
        if mute:
            payload[0] = 0x01
        else:
            payload[0] = 0x00
            
        # Update timestamp (offset 7)
        if len(payload) >= 15:
            struct.pack_into("<Q", payload, 7, new_samples)
            
        b104f.items[0] = payload
        
        # Add to playlist
        # Insert before the last element (which is the trailing dummy event)
        target_b1052.items.insert(len(target_b1052.items) - 1, new_event)
        
        # We must increment the count in the 1052 header!
        header = bytearray(target_b1052.items[0])
        nlen = struct.unpack_from("<I", header, 0)[0]
        # The count is 4 bytes after the string
        count_offset = 4 + nlen
        current_count = struct.unpack_from("<I", header, count_offset)[0]
        struct.pack_into("<I", header, count_offset, current_count + 1)
        target_b1052.items[0] = header
        
        print(f"Duplicated clip '{clip_name}' to {hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}, mute={mute}")
        
        return clip_id

    
    def split_clip(self, track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff):
        """Splits a clip into two virtual clips at the specified timecode."""
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        cut_samples = engine.timecode_to_samples(cut_hh, cut_mm, cut_ss, cut_ff)
        
        # 1. Find the clip definition (0x2629)
        b262a = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262a), None)
        if not b262a:
            raise ValueError("No clip list (0x262a) found.")
            
        orig_2629 = None
        orig_clip_id = -1
        clips = [c for c in b262a.items if isinstance(c, PTBlock) and c.content_type == 0x2629]
        for i, c in enumerate(clips):
            b2628 = next((x for x in c.items if isinstance(x, PTBlock) and x.content_type == 0x2628), None)
            if b2628:
                pl = b2628.items[0]
                nlen = struct.unpack_from("<I", pl, 0)[0]
                name = pl[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                if name == clip_name:
                    orig_2629 = c
                    orig_clip_id = i
                    break
                    
        if not orig_2629:
            raise ValueError(f"Clip '{clip_name}' not found.")
            
        # 2. Extract original length and timestamps
        b2628_orig = next((x for x in orig_2629.items if isinstance(x, PTBlock) and x.content_type == 0x2628), None)
        pl_orig = b2628_orig.items[0]
        nlen = struct.unpack_from("<I", pl_orig, 0)[0]
        offset = 4 + nlen
        
        # Ensure it is a 32-bit root clip format (00 00 30 44 00)
        hdr = pl_orig[offset:offset+5]
        if hdr != b"\x00\x00\x30\x44\x00":
            raise ValueError("Clip is already a split or has unsupported format (needs 32-bit root format).")
            
        orig_length = struct.unpack_from("<I", pl_orig, offset+5)[0]
        orig_ts1 = struct.unpack_from("<I", pl_orig, offset+9)[0]
        orig_ts2 = struct.unpack_from("<I", pl_orig, offset+13)[0]
        
        # 3. Find the event on the timeline that spans this absolute cut_samples
        target_event = None
        target_b1052 = None
        ev_idx = -1
        event_start_samples = 0
        
        b1054 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x1054), None)
        if not b1054:
            raise ValueError("No track map (0x1054) found.")
            
        for child in b1054.items:
            if isinstance(child, PTBlock) and child.content_type == 0x1052:
                for i, ev in enumerate(child.items):
                    if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                        b104f = next((c for c in ev.items if isinstance(c, PTBlock) and c.content_type == 0x104f), None)
                        if b104f:
                            payload = bytearray(b104f.items[0])
                            if len(payload) >= 15:
                                ev_clip_id = struct.unpack_from("<I", payload, 2)[0]
                                if ev_clip_id == orig_clip_id:
                                    ts = struct.unpack_from("<Q", payload, 7)[0]
                                    if ts < cut_samples < ts + orig_length:
                                        target_event = ev
                                        target_b1052 = child
                                        ev_idx = i
                                        event_start_samples = ts
                                        break
                if target_event: break
            
        if not target_event:
            raise ValueError(f"Could not find an instance of clip '{clip_name}' on the timeline that spans the requested cut timecode.")
            
        relative_cut_samples = cut_samples - event_start_samples
        
        # 4. Build Left Clip (-01)
        import copy, os
        clip_01 = copy.deepcopy(orig_2629)
        self._wipe_offsets_recursive(clip_01)
        # Fix UUID
        for i, item in enumerate(clip_01.items):
            if isinstance(item, bytearray) and len(item) == 48:
                new_ba = bytearray(item)
                new_ba[22:38] = os.urandom(16)
                clip_01.items[i] = bytes(new_ba)
        # Fix 0x2628
        b2628_01 = next(x for x in clip_01.items if isinstance(x, PTBlock) and x.content_type == 0x2628)
        new_name_01 = (clip_name + "-01").encode('ascii')
        pl_01 = bytearray()
        pl_01.extend(struct.pack("<I", len(new_name_01)))
        pl_01.extend(new_name_01)
        pl_01.extend(b"\x01\x00\x30\x44\x08") # Header
        # 24-bit length
        pl_01.append(relative_cut_samples & 0xFF)
        pl_01.append((relative_cut_samples >> 8) & 0xFF)
        pl_01.append((relative_cut_samples >> 16) & 0xFF)
        pl_01.append(0x00) # padding byte for the 4-byte slot in -01? Wait, -01 length is 4 bytes!
        # Ah, looking at my notes, -01 length is 4 bytes (a0 fe 15 00)
        pl_01[-1] = (relative_cut_samples >> 24) & 0xFF 
        pl_01.extend(struct.pack("<I", orig_ts1))
        pl_01.extend(struct.pack("<I", orig_ts1 | 0xFF000000))
        pl_01.extend(b"\xff\xff\xff\xff\xff\xff\xff\xfe") # 8 bytes padding
        # The "rest" after padding must have metadata zeroed out for sub-clips.
        # Parent's rest = pl_orig[offset+17:] contains UUID bytes that must NOT be copied.
        # Build the correct rest: ff 00000000 ffff 0400 0400 + 18 zero bytes + ffffffff 0000
        rest_01 = bytearray()
        rest_01.extend(b"\xff\x00\x00\x00\x00\xff\xff\x04\x00\x04\x00")  # 11 bytes
        rest_01.extend(b"\x00" * 21)  # 21 zero bytes (no UUID metadata for sub-clips)
        rest_01.extend(b"\xff\xff\xff\xff\x00\x00")  # 6 bytes
        pl_01.extend(rest_01)
        b2628_01.items[0] = bytes(pl_01)
        
        # 4. Build Right Clip (-02)
        clip_02 = copy.deepcopy(orig_2629)
        self._wipe_offsets_recursive(clip_02)
        for i, item in enumerate(clip_02.items):
            if isinstance(item, bytearray) and len(item) == 48:
                new_ba = bytearray(item)
                new_ba[22:38] = os.urandom(16)
                clip_02.items[i] = bytes(new_ba)
        
        b2628_02 = next(x for x in clip_02.items if isinstance(x, PTBlock) and x.content_type == 0x2628)
        new_name_02 = (clip_name + "-02").encode('ascii')
        pl_02 = bytearray()
        pl_02.extend(struct.pack("<I", len(new_name_02)))
        pl_02.extend(new_name_02)
        pl_02.extend(b"\x01\x30\x30\x44\x08")
        
        # 24-bit offset
        pl_02.append(relative_cut_samples & 0xFF)
        pl_02.append((relative_cut_samples >> 8) & 0xFF)
        pl_02.append((relative_cut_samples >> 16) & 0xFF)
        
        # 24-bit length
        rem_length = orig_length - relative_cut_samples
        pl_02.append(rem_length & 0xFF)
        pl_02.append((rem_length >> 8) & 0xFF)
        pl_02.append((rem_length >> 16) & 0xFF)
        
        # 32-bit TS1 and TS2 — these are TIMELINE ABSOLUTE timestamps,
        # NOT internal clip offsets. Must use orig_abs_ts (from 0x104f) + relative_cut_samples.
        # We don't have orig_abs_ts here yet; it's computed later in the timeline section.
        # So we store a placeholder and will patch it after timeline lookup.
        # Actually, we need to restructure: find orig_abs_ts FIRST.
        # For now, store relative_cut_samples offset — we'll fix this at the end.
        pl_02.extend(b"\x00" * 8)  # placeholder for ts1/ts2, will be patched below
        pl_02.extend(b"\xff\xff\xff\xff\xff\xff\xff\xff")  # 8 bytes ff padding
        # Build the correct rest with zeroed metadata
        rest_02 = bytearray()
        rest_02.extend(b"\xfe\xff\x00\x00\x00\x00\xff\xff\x04\x00\x04\x00")  # 12 bytes
        rest_02.extend(b"\x00" * 21)  # 21 zero bytes
        rest_02.extend(b"\xff\xff\xff\xff\x00\x00")  # 6 bytes
        pl_02.extend(rest_02)
        b2628_02.items[0] = bytes(pl_02)
        
        # 5. Append to 0x262a
        count_ba = bytearray(b262a.items[0])
        old_count = struct.unpack_from("<I", count_ba, 0)[0]
        
        idx_01 = old_count
        idx_02 = old_count + 1
        
        # Patch the new IDs into the 0x2629 payloads
        for i, it in enumerate(clip_01.items):
            if isinstance(it, (bytes, bytearray)) and len(it) >= 4:
                p = bytearray(it)
                struct.pack_into("<I", p, 0, idx_01)
                clip_01.items[i] = p
                break
                
        for i, it in enumerate(clip_02.items):
            if isinstance(it, (bytes, bytearray)) and len(it) >= 4:
                p = bytearray(it)
                struct.pack_into("<I", p, 0, idx_02)
                clip_02.items[i] = p
                break
        
        struct.pack_into("<I", count_ba, 0, old_count + 2)
        b262a.items[0] = count_ba
        b262a.items.append(clip_01)
        b262a.items.append(clip_02)
        
        # 6. Update Timeline (0x1052)
        b1054 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x1054), None)
        b1052 = None
        for track in [b for b in b1054.items if isinstance(b, PTBlock) and b.content_type == 0x1052]:
            if len(track.items) > 0 and isinstance(track.items[0], (bytes, bytearray)):
                hdr = track.items[0]
                nlen = struct.unpack_from("<I", hdr, 0)[0]
                if 4 + nlen <= len(hdr):
                    tname = hdr[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                    if tname == track_name:
                        b1052 = track
                        break
                        
        if not b1052:
            raise ValueError(f"Track '{track_name}' not found.")
            
        orig_1050 = None
        orig_1050_idx = -1
        orig_abs_ts = -1
        
        for i, ev in enumerate(b1052.items):
            if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                b104f = next((x for x in ev.items if isinstance(x, PTBlock) and x.content_type == 0x104f), None)
                if b104f:
                    p = b104f.items[0]
                    cid = struct.unpack_from("<I", p, 2)[0]
                    ts_check = struct.unpack_from('<Q', p, 7)[0]
                    if cid == orig_clip_id and ts_check == event_start_samples:
                        orig_1050 = ev
                        orig_1050_idx = i
                        orig_abs_ts = struct.unpack_from("<q", p, 7)[0]
                        orig_abs_ts = ts_check
                        
        if not orig_1050:
            raise ValueError(f"Clip '{clip_name}' is not placed on track '{track_name}'.")
        
        cut_abs_ts = orig_abs_ts + relative_cut_samples
        
        # Now patch clip -02's ts1/ts2 with the correct TIMELINE absolute timestamp
        pl_02_patched = bytearray(b2628_02.items[0])
        nlen_02 = struct.unpack_from("<I", pl_02_patched, 0)[0]
        # ts1 and ts2 are at offset name_len + 4 + 5 + 3 + 3 = name_len + 15
        ts_offset = 4 + nlen_02 + 5 + 3 + 3  # after name + header + src_offset_24 + length_24
        struct.pack_into("<I", pl_02_patched, ts_offset, cut_abs_ts)
        struct.pack_into("<I", pl_02_patched, ts_offset + 4, cut_abs_ts)
        b2628_02.items[0] = bytes(pl_02_patched)
            
        # Create ev_02 as a deep copy with new clip ID and timestamp
        ev_02 = copy.deepcopy(orig_1050)
        self._wipe_offsets_recursive(ev_02)
        p_02 = bytearray(ev_02.items[0].items[0])
        struct.pack_into("<I", p_02, 2, idx_02)
        struct.pack_into("<q", p_02, 7, cut_abs_ts)
        ev_02.items[0].items[0] = bytearray(p_02)
        
        # Mutate the original event IN PLACE to become ev_01.
        # CRITICAL: We must NOT pop() the original event, because its pointer
        # exists in the 0x0002 table. Removing it creates a dangling pointer
        # that causes "Magic ID does not match".
        orig_104f = next(x for x in orig_1050.items if isinstance(x, PTBlock) and x.content_type == 0x104f)
        p_orig = bytearray(orig_104f.items[0])
        struct.pack_into("<I", p_orig, 2, idx_01)
        orig_104f.items[0] = bytearray(p_orig)
        
        # Insert ev_02 AFTER the mutated orig (now ev_01)
        b1052.items.insert(orig_1050_idx + 1, ev_02)
        
        # Update event counter (+1, not +2, because we only inserted one new event)
        hdr = bytearray(b1052.items[0])
        nlen = struct.unpack_from("<I", hdr, 0)[0]
        count_offset = 4 + nlen
        old_count = struct.unpack_from("<I", hdr, count_offset)[0]
        struct.pack_into("<I", hdr, count_offset, old_count + 1)
        b1052.items[0] = bytearray(hdr)
        
        return orig_clip_id, idx_01, idx_02, cut_abs_ts
        
    def add_crossfade(self, track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff, fade_hh, fade_mm, fade_ss, fade_ff):
        """Splits a clip and injects a crossfade at the split point."""
        # 1. Split the clip
        orig_clip_id, idx_01, idx_02, cut_abs_ts = self.split_clip(track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff)
        
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        fade_samples = engine.timecode_to_samples(fade_hh, fade_mm, fade_ss, fade_ff)
        half_fade = fade_samples // 2
        
        # 2. Geometry (0x262f)
        b2630 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x2630), None)
        count_ba = bytearray(b2630.items[0])
        old_count = struct.unpack_from("<I", count_ba, 0)[0]
        struct.pack_into("<I", count_ba, 0, old_count + 1)
        b2630.items[0] = count_ba
        
        geom_pl = bytearray(36)
        geom_pl[0:7] = b"\x00\x00\x00\x00\x00\x33\x00"
        struct.pack_into("<I", geom_pl, 8, half_fade)
        geom_pl[11] = 0 # override top byte to keep it 24-bit
        struct.pack_into("<I", geom_pl, 11, fade_samples)
        geom_pl[14] = 0 # override top byte
        geom_pl[14] = 0x01
        geom_pl[15] = 0x01 # Standard Equal Power
        
        new_262f = PTBlock(2, 0x262f, 0)
        new_262f.items = [bytes(geom_pl)]
        b2630.items.append(new_262f)
        
        # 3. Fade Event (0x1050)
        b1054 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x1054), None)
        b1052 = None
        for track in [b for b in b1054.items if isinstance(b, PTBlock) and b.content_type == 0x1052]:
            if len(track.items) > 0 and isinstance(track.items[0], (bytes, bytearray)):
                hdr = track.items[0]
                nlen = struct.unpack_from("<I", hdr, 0)[0]
                if 4 + nlen <= len(hdr):
                    tname = hdr[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                    if tname == track_name:
                        b1052 = track
                        break
                        
        if not b1052:
            raise ValueError(f"Track '{track_name}' not found.")
                        
        # Find insertion point (just before ev_02)
        ev_idx = -1
        for i, ev in enumerate(b1052.items):
            if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                b104f = next((x for x in ev.items if isinstance(x, PTBlock) and x.content_type == 0x104f), None)
                if b104f:
                    p = b104f.items[0]
                    cid = struct.unpack_from("<I", p, 2)[0]
                    if cid == idx_02:
                        ev_idx = i
                        break
                        
        p104f = bytearray(35)
        p104f[0] = 0x01
        struct.pack_into("<I", p104f, 2, orig_clip_id)  # Points to PARENT clip, not -01
        struct.pack_into("<q", p104f, 7, cut_abs_ts)
        p104f[15:35] = bytes.fromhex("01feff00000000ffffffffffffffff0000000000")
        
        new_104f = PTBlock(10, 0x104f, 0)  # block_type=10, NOT 3
        new_104f.items = [bytes(p104f)]
        
        new_1050 = PTBlock(3, 0x1050, 0)
        new_1050.items = [new_104f, b"\x01\x01\x01"]
        
        # Insert exactly at ev_02's index, pushing ev_02 down
        b1052.items.insert(ev_idx, new_1050)
        
        hdr = bytearray(b1052.items[0])
        nlen = struct.unpack_from("<I", hdr, 0)[0]
        count_offset = 4 + nlen
        old_count = struct.unpack_from("<I", hdr, count_offset)[0]
        struct.pack_into("<I", hdr, count_offset, old_count + 1)
        b1052.items[0] = bytearray(hdr)
                
    def add_fade(self, track_name, clip_name, target_hh, target_mm, target_ss, target_ff, fade_type="in", duration_hh=0, duration_mm=0, duration_ss=0, duration_ff=0, fade_shape="power"):
        import copy
        import struct

        # 1. Convert duration to samples
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        fade_length_samples = engine.timecode_to_samples(duration_hh, duration_mm, duration_ss, duration_ff)
        if fade_length_samples <= 0:
            fade_length_samples = self.sample_rate

        fade_type = fade_type.lower()
        if fade_type not in ["in", "out"]:
            raise ValueError("fade_type must be 'in' or 'out'")
            
        shape_byte = 0x01 if fade_shape == "power" else 0x02
            
        target_samples = engine.timecode_to_samples(target_hh, target_mm, target_ss, target_ff)

        def find_blocks(item, ctype):
            found = []
            if isinstance(item, PTBlock):
                if item.content_type == ctype:
                    found.append(item)
                for child in item.items:
                    found.extend(find_blocks(child, ctype))
            return found

        # We no longer need to find the parent clip ID. 
        # The ID in a fade event (bt=10) is ACTUALLY the index of the fade geometry in 0x2630!
        b2630 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x2630), None)
        if not b2630: raise ValueError("No 0x2630")
        
        count_ba = bytearray(b2630.items[0])
        fade_geometry_id = struct.unpack_from("<I", count_ba, 0)[0] # This will be the new ID

        # 2. Find track and event on timeline
        b1054 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x1054), None)
        b1052 = None
        for b in find_blocks(b1054, 0x1052):
            if len(b.items) > 0 and isinstance(b.items[0], bytearray):
                hdr = b.items[0]
                nlen = struct.unpack_from("<I", hdr, 0)[0]
                if 4 + nlen <= len(hdr):
                    tname = hdr[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                    if tname == track_name:
                        b1052 = b
                        break

        if not b1052: raise ValueError(f"Track '{track_name}' not found")

        # 3. Build geometry (0x262f)
        if fade_type == "in":
            # 22-byte format
            fade_geom_payload = bytearray(bytes.fromhex("00000000002000000000030100000000000000000000"))
            fade_geom_payload[8:10] = struct.pack("<H", fade_length_samples & 0xFFFF)
            fade_geom_payload[11] = shape_byte
            fade_ts = target_samples
            insert_after = False
        else:
            # Fade Out: 26-byte format discovered in Phase 10
            fade_geom_payload = bytearray(bytes.fromhex("0000000000220000000000000202000000000000000000000000"))
            # Length is repeated twice? offset 8 and offset 10 (16-bit each)
            length_bytes = struct.pack("<H", fade_length_samples & 0xFFFF)
            fade_geom_payload[8:10] = length_bytes
            fade_geom_payload[10:12] = length_bytes
            # Note: offset 12 is flags (02), offset 13 is shape. In the hex it's 02 02. Let's patch offset 13.
            fade_geom_payload[13] = shape_byte
            fade_ts = target_samples
            insert_after = True

        new_262f = PTBlock(2, 0x262f)
        new_262f.original_offset = -1
        new_262f.items = [fade_geom_payload]

        struct.pack_into("<I", count_ba, 0, fade_geometry_id + 1)
        b2630.items[0] = count_ba
        b2630.items.append(new_262f)

        # 4. Build fade event (0x1050)
        p104f = bytearray(35)
        p104f[0] = 0x01
        struct.pack_into("<I", p104f, 2, fade_geometry_id)  # THIS IS THE GEOMETRY ID, NOT CLIP ID!
        struct.pack_into("<q", p104f, 7, fade_ts)
        p104f[15:35] = bytes.fromhex("01feff00000000ffffffffffffffff0000000000")

        new_104f = PTBlock(10, 0x104f, 0)
        new_104f.original_offset = -1
        new_104f.items = [bytes(p104f)]

        new_1050 = PTBlock(3, 0x1050, 0)
        new_1050.original_offset = -1
        new_1050.items = [new_104f, b"\x01\x01\x01"]

        # Insert safely by chronological order of timestamp!
        # Instead of searching for the clip and inserting next to it, 
        # just find the correct insertion index to maintain chronological sort.
        insert_idx = 1 # Start after header (index 0)
        for i in range(1, len(b1052.items)):
            ev = b1052.items[i]
            if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                b104f = next((x for x in ev.items if isinstance(x, PTBlock) and x.content_type == 0x104f), None)
                if b104f and len(b104f.items) > 0 and isinstance(b104f.items[0], (bytes, bytearray)) and len(b104f.items[0]) > 14:
                    ev_ts = struct.unpack_from("<Q", b104f.items[0], 7)[0]
                    # If the timestamp of the existing event is strictly greater than ours, we should insert here.
                    # Wait, for Fade In (insert_after=False), we should be BEFORE an event at the exact same timestamp.
                    # For Fade Out (insert_after=True), we should be AFTER an event at the exact same timestamp.
                    if ev_ts > fade_ts:
                        insert_idx = i
                        break
                    elif ev_ts == fade_ts and not insert_after:
                        insert_idx = i
                        break
                    else:
                        insert_idx = i + 1
        
        b1052.items.insert(insert_idx, new_1050)

        hdr = bytearray(b1052.items[0])
        name_len = struct.unpack_from("<I", hdr, 0)[0]
        count_offset = 4 + name_len
        old_count = struct.unpack_from("<I", hdr, count_offset)[0]
        struct.pack_into("<I", hdr, count_offset, old_count + 1)
        b1052.items[0] = bytearray(hdr)

    def set_clip_gain(self, clip_name, float_gain_db):
        import struct
        
        # Resolve the clip_id (0-based index of the 2629 block inside the 262a block)
        b262a = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x262a), None)
        if not b262a:
            raise ValueError("No clips (0x262a) found in session.")
            
        target_b2628 = None
        
        for child in b262a.items:
            if isinstance(child, PTBlock) and child.content_type == 0x2629:
                b2628 = next((c for c in child.items if isinstance(c, PTBlock) and c.content_type == 0x2628), None)
                if b2628:
                    payload = getattr(b2628, 'items', [b""])[0]
                    if isinstance(payload, (bytes, bytearray)) and len(payload) >= 4:
                        nlen = struct.unpack_from("<I", payload, 0)[0]
                        if 4 + nlen <= len(payload):
                            name = payload[4:4+nlen].decode('ascii', 'ignore').strip('\x00')
                            if name == clip_name:
                                target_b2628 = b2628
                                break
                                
        if not target_b2628:
            raise ValueError(f"Clip '{clip_name}' not found.")
            
        # Float resolution
        if str(float_gain_db).lower() in ('-inf', '-infinity'):
            val = struct.unpack('<f', bytes.fromhex('f41a91c3'))[0] # -290.21057
        else:
            val = float(float_gain_db)
            
        # Build 30-byte payload for a SINGLE point
        point_meta = bytes.fromhex("0146010016000000000001000000040000000000000000000000")
        float_bytes = struct.pack('<f', val)
        new_point = point_meta + float_bytes
        
        # Inject into root block 0x2637 (List of points)
        b2637 = next((r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x2637), None)
        if not b2637:
            b2637 = PTBlock(1, 0x2637, 0)
            b2637.items.append(bytearray(b'\x00\x00\x00\x00')) # default 0 points
            self.root_items.append(b2637)
            
        current_payload = bytearray(b2637.items[0])
        num_points = struct.unpack_from("<I", current_payload, 0)[0]
        
        automation_index = num_points
        
        current_payload.extend(new_point)
        struct.pack_into("<I", current_payload, 0, num_points + 1)
        
        b2637.items[0] = current_payload
            
        # Now, flip the switch! 
        # The 2628 block links the clip to its automation curve via the index.
        # The index is ALWAYS stored as a signed 32-bit int at the very end of the 2628 payload (offset -6 from end).
        # -1 (0xffffffff) means no clip gain.
        clip_payload = bytearray(target_b2628.items[0])
        import struct
        struct.pack_into('<i', clip_payload, len(clip_payload)-6, automation_index)
        target_b2628.items[0] = clip_payload
            
        print(f"Applied Clip Gain of {val} dB to clip '{clip_name}'. Mapped to automation index {automation_index}.")

    def add_volume_node(self, track_name, hh, mm, ss, ff, db_value):
        import struct
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        target_samples = engine.timecode_to_samples(hh, mm, ss, ff)

        target_val = int(round(db_value * 10))
        # Ensure it fits in signed 16-bit
        target_val = max(-32768, min(32767, target_val))

        def find_blocks(item, ctype):
            found = []
            if isinstance(item, PTBlock):
                if item.content_type == ctype:
                    found.append(item)
                for child in item.items:
                    found.extend(find_blocks(child, ctype))
            return found

        all_261c = []
        for r in self.root_items:
            all_261c.extend(find_blocks(r, 0x261c))

        found_260a = None
        for b261c in all_261c:
            # Check track name in 0x2619
            b2619s = find_blocks(b261c, 0x2619)
            matched = False
            for b2619 in b2619s:
                if len(b2619.items) > 0 and isinstance(b2619.items[0], bytearray):
                    payload = b2619.items[0]
                    if len(payload) >= 4:
                        name_len = struct.unpack_from("<I", payload, 0)[0]
                        if 4 + name_len <= len(payload):
                            try:
                                name = payload[4:4+name_len].decode('ascii').strip('\x00')
                                if name == track_name:
                                    matched = True
                                    break
                            except:
                                pass

            if matched:
                # We found the track! Find 0x260d
                b260ds = find_blocks(b261c, 0x260d)
                if b260ds:
                    # Inside 0x260d, the FIRST 0x260a is Volume automation.
                    # Be careful: we must iterate directly on the block tree to find the first 260a
                    # because find_blocks traverses everything and order is preserved.
                    all_260a = find_blocks(b260ds[0], 0x260a)
                    if all_260a:
                        found_260a = all_260a[0]
                        break

        if found_260a is None:
            raise ValueError(f"Track '{track_name}' or its volume automation block not found.")

        # Parse existing 260a payload
        if len(found_260a.items) == 0 or not isinstance(found_260a.items[0], bytearray):
            raise ValueError("Invalid 260a payload format.")

        payload = found_260a.items[0]
        if len(payload) < 22:
            raise ValueError("260a payload too short.")

        magic = payload[0:4]
        n_nodes = struct.unpack_from("<I", payload, 10)[0]
        flags = bytearray(payload[14:22])

        nodes = []
        for i in range(n_nodes):
            offset = 22 + (i * 6)
            if offset + 6 <= len(payload):
                ts = struct.unpack_from("<I", payload, offset)[0]
                val = struct.unpack_from("<h", payload, offset + 4)[0]
                nodes.append((ts, val))

        # Insert new node or update existing
        nodes = [n for n in nodes if n[0] != target_samples] # Remove if exact ts exists
        nodes.append((target_samples, target_val))
        nodes.sort(key=lambda x: x[0])

        # Update segment count in flags (bytes 2-5 of the 8-byte flag block)
        if len(nodes) > 1:
            struct.pack_into("<I", flags, 2, len(nodes) - 1)
        else:
            struct.pack_into("<I", flags, 2, 0)
            
        # Rebuild payload
        new_payload = bytearray()
        new_payload.extend(magic)
        new_payload.extend(struct.pack("<I", len(nodes) * 6 + 14))
        new_payload.extend(b"\x00\x00")
        new_payload.extend(struct.pack("<I", len(nodes)))
        new_payload.extend(flags)

        for ts, val in nodes:
            new_payload.extend(struct.pack("<I", ts))
            new_payload.extend(struct.pack("<h", val))

        # The automation payload always ends with exactly 2 null bytes, regardless of length
        new_payload.extend(b"\x00\x00")

        found_260a.items[0] = new_payload
        return len(nodes)

    def _wipe_offsets_recursive(self, node):
        if isinstance(node, PTBlock):
            node.original_offset = -1
            for child in node.items:
                self._wipe_offsets_recursive(child)

    def wipe_all_offsets(self):
        """Set original_offset=-1 on every PTBlock in the session tree.
        Call this before save() whenever blocks are added or removed, so the
        pointer rebuilder generates a clean mapping without stale addresses."""
        def _wipe(node):
            if isinstance(node, PTBlock):
                node.original_offset = -1
                for child in node.items:
                    _wipe(child)
        for item in self.root_items:
            _wipe(item)

    def _decode_0002_records(self, payload):
        """
        Parse the structured records inside block 0x0002.
        Each record has an 8-byte prefix (00 00 00 01 04 00 01 00) followed by
        a 32-bit LE pointer to a block's absolute offset in the file.
        Returns a list of (prefix_start, pointer_offset_in_payload) tuples.
        """
        PREFIX = bytes.fromhex("0000000104000100")
        records = []
        pos = 0
        while pos < len(payload):
            idx = payload.find(PREFIX, pos)
            if idx == -1:
                break
            ptr_pos = idx + len(PREFIX)  # position of the 32-bit pointer within payload
            records.append((idx, ptr_pos))
            pos = idx + 1
        return records

    def _purge_0002_records(self, b0002, removed_offsets, is_bigendian):
        """
        Strip 0x0002 records whose pointer targets a block that no longer
        exists in the tree (deleted via e.g. delete_clip_group()).

        _rebuild_0002() only ever *patches* pointer values for blocks still
        present in global_mapping; it never drops a record. If we pop() a
        block without removing its record here first, the record keeps
        pointing at whatever now lives at that stale offset -> "Magic ID
        does not match" in Pro Tools. Must run BEFORE _rebuild_0002().
        """
        import struct
        if not removed_offsets:
            return 0
        fmt = ">I" if is_bigendian else "<I"
        fmt_type = ">H" if is_bigendian else "<H"
        fmt_size = ">I" if is_bigendian else "<I"

        # Reassemble the full original payload, same approach as
        # _rebuild_0002: phantom zero-size PTBlocks get serialized back to
        # their original 7 raw bytes first.
        payload = bytearray()
        for item in b0002.items:
            if isinstance(item, bytearray):
                payload.extend(item)
            elif isinstance(item, PTBlock):
                payload.append(0x5a)
                payload.extend(struct.pack(fmt_type, item.block_type))
                payload.extend(struct.pack(fmt_size, 0))

        records = self._decode_0002_records(payload)
        if not records:
            b0002.items = [payload]
            return 0

        removed_set = set(removed_offsets)

        # Each record's span runs from its own prefix start up to the next
        # record's prefix start (metadata length is variable per spec, so
        # we can't assume a fixed record size).
        spans = []
        for i, (prefix_start, ptr_pos) in enumerate(records):
            end = records[i + 1][0] if i + 1 < len(records) else len(payload)
            spans.append((prefix_start, end, ptr_pos))

        new_payload = bytearray()
        purged = 0
        last_end = 0
        for (start, end, ptr_pos) in spans:
            new_payload.extend(payload[last_end:start])
            if ptr_pos + 4 <= len(payload):
                ptr_val = struct.unpack_from(fmt, payload, ptr_pos)[0]
            else:
                ptr_val = None
            if ptr_val is not None and ptr_val in removed_set:
                purged += 1  # drop this record's bytes entirely
            else:
                new_payload.extend(payload[start:end])
            last_end = end
        new_payload.extend(payload[last_end:])

        b0002.items = [new_payload]
        return purged

    def _rebuild_0002(self, b0002, global_mapping, is_bigendian):
        """
        Rebuild the 0x0002 block payload by patching only the 32-bit pointers
        at their known structural positions. Never touches any other byte.

        Important: the parser may split 0x0002's payload into alternating
        bytearrays and zero-size phantom PTBlocks (when it encounters 0x5a bytes
        inside the raw payload that happen to parse as valid zero-size blocks).
        We must reassemble the FULL original byte sequence before patching, then
        replace b0002.items with a single bytearray so to_bytes() serializes it
        correctly without duplication.
        """
        import struct
        fmt = ">I" if is_bigendian else "<I"
        fmt_type = ">H" if is_bigendian else "<H"
        fmt_size = ">I" if is_bigendian else "<I"

        # Reassemble the full original payload, including bytes occupied by
        # phantom zero-size PTBlock children (serialized back to their 7 raw bytes).
        payload = bytearray()
        for item in b0002.items:
            if isinstance(item, bytearray):
                payload.extend(item)
            elif isinstance(item, PTBlock):
                # Zero-size phantom block: serialize back to its original 7 bytes.
                # These are 0x5a bytes in the raw data that the parser consumed.
                payload.append(0x5a)
                payload.extend(struct.pack(fmt_type, item.block_type))
                payload.extend(struct.pack(fmt_size, 0))  # size = 0, no content_type

        records = self._decode_0002_records(payload)

        patched_count = 0
        for (prefix_start, ptr_pos) in records:
            if ptr_pos + 4 > len(payload):
                continue
            old_ptr = struct.unpack_from(fmt, payload, ptr_pos)[0]
            if old_ptr in global_mapping and global_mapping[old_ptr] != old_ptr:
                new_ptr = global_mapping[old_ptr]
                struct.pack_into(fmt, payload, ptr_pos, new_ptr)
                patched_count += 1

        # Replace all items with a single bytearray so to_bytes() does not
        # re-expand the phantom blocks a second time.
        b0002.items = [payload]

        return patched_count

    def save(self, out_path):
        import struct

        # Pass 1: Compute new absolute offsets for every block.
        # to_bytes() builds a mapping of {old_original_offset -> new_computed_offset}
        # for every block that has original_offset > 0.
        global_mapping = {}
        current_offset = 0x14
        for item in self.root_items:
            if isinstance(item, bytearray):
                current_offset += len(item)
            else:
                block_bytes, mapping = item.to_bytes(self.is_bigendian, current_offset)
                global_mapping.update(mapping)
                current_offset += len(block_bytes)

        # Pass 2: Rebuild the 0x0002 pointer table using the computed mapping.
        # This is the ONLY place we modify pointers — targeted and safe.
        b0002 = next(
            (r for r in self.root_items if isinstance(r, PTBlock) and r.content_type == 0x0002),
            None
        )
        if b0002:
            removed = getattr(self, '_removed_offsets', [])
            if removed:
                purged = self._purge_0002_records(b0002, removed, self.is_bigendian)
                if purged:
                    print(f"Purged {purged} stale 0x0002 record(s) for deleted block(s).")
                self._removed_offsets = []
            self._rebuild_0002(b0002, global_mapping, self.is_bigendian)

        # Pass 3: Fix the 0x0001 block's pointer to 0x0002.
        # The 0x0001 block's payload (bytes 7-10 relative to ZMARK) is a 32-bit
        # absolute offset pointing to the 0x0002 block.
        block_0002_idx = next(
            (i for i, r in enumerate(self.root_items)
             if isinstance(r, PTBlock) and r.content_type == 0x0002),
            -1
        )

        # Pass 4: Final serialization — build the output binary
        out_blocks = []
        current_offset = 0x14
        for item in self.root_items:
            if isinstance(item, bytearray):
                out_blocks.append(item)
                current_offset += len(item)
            else:
                block_bytes, _ = item.to_bytes(self.is_bigendian, current_offset)
                out_blocks.append(bytearray(block_bytes))
                current_offset += len(block_bytes)

        # Patch 0x0001 -> 0x0002 pointer after we know the exact offset of 0x0002
        if block_0002_idx != -1:
            new_0002_offset = 0x14 + sum(len(b) for b in out_blocks[:block_0002_idx])
            # The first block is 0x0001; its pointer is at bytes [7:11] of the serialized block
            fmt_ptr = ">I" if self.is_bigendian else "<I"
            first_block = out_blocks[0]
            first_block[7:11] = struct.pack(fmt_ptr, new_0002_offset)

        # Assemble and write
        out_data = bytearray()
        out_data.extend(self.data[:0x14])
        for block_bytes in out_blocks:
            out_data.extend(block_bytes)

        print(f"Re-encrypting and saving to {out_path}...")
        xor_session(out_data, out_path)
        print("Done!")

if __name__ == "__main__":
    # Test loading and saving a session without modification (bit-perfect test)
    import sys
    if len(sys.argv) > 2:
        sess = ProToolsSession(sys.argv[1])
        sess.save(sys.argv[2])
    else:
        print("Usage: python pt_api.py <input.ptx> <output.ptx>")
