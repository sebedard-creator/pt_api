import sys
from pt_api import ProToolsSession

def main():
    base_file = r"Y:\SHARE TO NETWORK\PT_Decryp_DATA\22_super_session_pt2\TEST_12_CROSSFADE_BASE.ptx"
    out_file = r"Y:\SHARE TO NETWORK\PT_Decryp_DATA\22_super_session_pt2\TEST_12_MUTE_OUT.ptx"
    
    print(f"Loading {base_file}...")
    session = ProToolsSession(base_file)
    
    clip_name = "TEST_KICK.wav"
    print(f"Mutating {clip_name}...")
    
    # Mute
    count = session.mute_clip(clip_name, mute=True)
    
    print(f"Muted {count} instances of {clip_name}.")
    
    print(f"Sauvegarde dans {out_file}...")
    session.save(out_file)
    print("Terminé avec succès !")

if __name__ == "__main__":
    main()
