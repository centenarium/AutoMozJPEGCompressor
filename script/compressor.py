import os
import subprocess
import shutil
import time
import sys
import signal
import multiprocessing
from pathlib import Path
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor, as_completed

VALID_EXT = {".png", ".jpg", ".jpeg"}
CLR = {
    "G": "\033[92m", "R": "\033[91m", "Y": "\033[93m", 
    "W": "\033[0m", "B": "\033[94m", "C": "\033[96m", "P": "\033[95m"
}

# 2. Signal handler to abort safely on Ctrl+C
def signal_handler(sig, frame):
    print(f"\n{CLR['R']}Aborting and cleaning up related processes...{CLR['W']}")
    # The pool.shutdown(wait=False, cancel_futures=True) handles this inside main
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

if getattr(sys, 'frozen', False):
    # If .exe, look in the same folder as the .exe
    base_dir = Path(sys.executable).parent
else:
    # If .py, look in the same folder as the .py
    base_dir = Path(__file__).parent

CJPEG_PATH = base_dir / "cjpeg.exe"

if not CJPEG_PATH.exists():
    print(f"Warning: cjpeg.exe not found at {CJPEG_PATH}. Falling back to system path.")
    CJPEG_CMD = "cjpeg" # Fallback to system path
else:
    CJPEG_CMD = str(CJPEG_PATH)

def get_user_input():
    print(f"{CLR['B']}--- AutoMJPEG Compressor ---{CLR['W']}")
    
    # 1. Directory selection
    path_input = input(f"Paste the input directory path (or Enter for current directory): ").strip().replace('"', '')
    input_dir = Path(path_input).resolve() if path_input else Path.cwd()
    
    if not input_dir.exists():
        print(f"{CLR['R']}Error: Path does not exist.{CLR['W']}")
        exit(1)

    # 2. Quality selection
    print(f"\n{CLR['C']}Select Compression Quality:{CLR['W']}")
    print(f"[{CLR['G']}95{CLR['W']}] Lowest     - Minimal compression, large files.")
    print(f"[{CLR['G']}87{CLR['W']}] Balanced   - {CLR['Y']}Recommended{CLR['W']}. Imperceptible quality loss and small file sizes.")
    print(f"[{CLR['Y']}75{CLR['W']}] Compact    - Major reduction in size, noticeable loss in detail.")
    print(f"[{CLR['R']}50{CLR['W']}] Aggressive - Smallest files, substantial quality loss.")
    
    quality_input = input(f"Enter quality (1-100) [Default 87]: ").strip()
    quality = int(quality_input) if quality_input.isdigit() else 87

    # 3. Copy other files apart from images?
    copy_non_images = input("\nCopy ALL non-image files to output? (y/n): ").lower() == 'y'
    
    OUT_NAME = "compressed_output"
    output_dir = input_dir / OUT_NAME
    log_file = input_dir / "compression_log.txt"
    
    # Scan target directory
    print(f"\nScanning {CLR['Y']}{input_dir}{CLR['W']}...")
    all_files = []
    image_count = 0
    for root, dirs, files in os.walk(input_dir):
        if OUT_NAME in dirs:
            dirs.remove(OUT_NAME)

        for name in files:
            p = Path(root) / name
            all_files.append(p)
            if p.suffix.lower() in VALID_EXT:
                image_count += 1

    print(f"Found {CLR['G']}{image_count}{CLR['W']} images and {len(all_files) - image_count} other files.")
    print(f"Output will be saved to: {CLR['C']}{output_dir.name}{CLR['W']}")
    
    # 4. Final input
    confirm = input(f"Proceed? (y/n): ").lower()
    if confirm != 'y':
        print("Aborted.")
        exit()

    return input_dir, output_dir, all_files, copy_non_images, quality, log_file

def process_one(src, input_dir, output_dir, copy_non_images, quality):
    ext = src.suffix.lower()
    rel = src.relative_to(input_dir)

    # Compress images
    if ext in VALID_EXT:
        dst = output_dir / "compressed_images" / rel.with_suffix(".jpg")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            in_size = src.stat().st_size
            with open(dst, "wb") as out:
                subprocess.run(
                    [CJPEG_CMD, "-quality", str(quality), "-optimize", "-progressive", str(src)],
                    stdout=out, stderr=subprocess.PIPE, check=True
                )
            return ("ok", src, in_size, dst.stat().st_size)
        except Exception as e:
            return ("fail", src, str(e), None)

    # Copy non-image files over
    if copy_non_images:
        dst = (output_dir / "non_images") / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            return ("copied", src, None, None)
        except Exception as e:
            return ("fail", src, str(e), None)
            
    return ("skip", src, None, None)

def main():
    input_dir, output_dir, all_files, copy_non_images, quality, log_file = get_user_input()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resume logic. Have we tried compressing these images before?
    files_to_process = []
    skipped_count = 0
    
    for f in all_files:
        rel = f.relative_to(input_dir)
        # Determine paths for images and non-images
        if f.suffix.lower() in VALID_EXT:
            dst = output_dir / "compressed_images" / rel.with_suffix(".jpg")
        else:
            dst = (output_dir / "non_images") / rel
        
        if dst.exists():
            skipped_count += 1
            continue
            
        files_to_process.append(f)

    if skipped_count > 0:
        print(f"{CLR['Y']}Resuming: {skipped_count} files already exist in output and will be skipped.{CLR['W']}")

    processed, failed, input_bytes, output_bytes = 0, 0, 0, 0
    start_time = time.time()
    workers = max(1, multiprocessing.cpu_count() - 1)

    with open(log_file, "w", encoding="utf-8") as log:
        log.write(f"\n--- Session: {time.ctime()} | Quality: {quality} ---\n")

        with ProcessPoolExecutor(max_workers=workers) as pool:
            try:
                futures = [pool.submit(process_one, f, input_dir, output_dir, copy_non_images, quality) for f in files_to_process]
                
                for future in tqdm(as_completed(futures), total=len(futures), desc="Processing"):
                    status, src, a, b = future.result()
                    if status == "ok":
                        processed += 1
                        input_bytes += a
                        output_bytes += b
                    elif status == "fail":
                        failed += 1
                        log.write(f"[ERROR] {src} -> {a}\n")
                    elif status == "copied":
                        log.write(f"[COPIED] {src}\n")

            except KeyboardInterrupt:
                print(f"\n{CLR['R']}Stopping processes safely...{CLR['W']}")
                pool.shutdown(wait=False, cancel_futures=True)
                sys.exit(1)

        # Summary 
        elapsed = time.time() - start_time
        print(f"\n{CLR['G']}Done!{CLR['W']}")
        print(f"Images Processed: {processed}")
        
        if input_bytes > 0:
            reduction = 100 - (100 * output_bytes / input_bytes)
            print(f"Space Saved: {reduction:.2f}% ({(input_bytes - output_bytes)/1024/1024:.2f} MB)")
        
        print(f"Detailed log saved to: {log_file}")
        log.write(f"Elapsed time     : {elapsed/60:.2f} minutes\n") 

        print("\n" + "-"*30)
        
        final_choice = input(f"Did you enjoy using this tool? ({CLR['G']}y{CLR['W']}/{CLR['R']}n{CLR['W']}): ").lower().strip()
        
        if final_choice == 'y':
            print(f"{CLR['G']}{descramble(SCRAMBLED_THANKS)}{CLR['W']}")
        else:
            print(f"{CLR['R']}{descramble(SCRAMBLED_WHY)}{CLR['W']}")
            
        

def descramble(scrambled_list):
    sorted_lines = sorted(scrambled_list, key=lambda x: x[0])
    return "\n".join(line[1] for line in sorted_lines)


SCRAMBLED_THANKS = [
(1, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣦⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣯⣿⣷⣯⣿⣿⣿⣿⣵⢞⡶⡰⢦⡔⢠⠒⡰⢊⡖⡱⢎⡵⣚⠴⣣⠞⣦⡙⢦⡓⢮"),
(2, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣳⠵⣚⠵⠦⠵⣌⡣⢞⡱⣋⠶⣩⠞⣥⢛⡴⣙⠶⣩⠗"),
(3, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠆⠀⠀⠀⠀⠀⠀⢠⣴⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣿⣷⡺⠉⠉⠐⢿⡰⢥⢣⡛⡴⣋⠶⣩⠖⣭⢚⡥⣛"),
(4, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⠀⠀⠀⢤⣠⣶⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣆⠀⠂⢈⡗⣎⢧⡙⢶⡩⢞⡥⣛⠴⣋⠴⣣"),
(9, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡻⣞⡻⣞⢯⠖⣿⡇⢣⠀⠡⠀⠄⠀⠀⠂⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠃⠀⠀⠀⠱⡞⣿⣿⣿⣿⣿⣿⣿⣷⣙⡎⢦⠱⢌⡃⢎"),
(15, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢘⣿⣿⣿⣿⣿⣿⣳⢯⣟⣾⡿⣾⣄⡡⢊⢄⡊⠄⢌⠒⡄⠣⠠⢄⡁⠆⡐⢁⡀⠠⠀⠄⠠⢀⠀⠀⠀⠀⡀⣀⣀⡀⣄⠀⠀⠀⠌⠠⢿⣿⣿⣿⣿⣿⣿⠋⡄⠣⠌⢂⠅"),
(16, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⣿⣿⣿⣷⠿⣯⣿⣿⣿⣿⣿⣿⣿⣶⣌⡼⣠⢣⢌⡱⢡⠆⡰⢡⠐⡠⡐⡡⠎⣌⣆⣥⣦⣶⣾⣿⣿⣿⣿⣭⣯⣽⣦⣤⡀⠂⠌⣿⣿⣿⣿⣿⣧⠘⡄⠣⡘⢠⠂"),
(11, r" ⠙⠛⠛⠛⠛⠛⠿⠿⢿⣿⣟⣧⢻⣿⣿⣿⣿⣿⣿⣿⣏⡟⣬⠳⡽⢮⣷⡿⣾⠋⢈⠒⠠⠐⢄⠠⠁⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⠀⠀⠀⠀⠐⡀⠠⢁⢳⣿⣿⣿⣿⣿⣿⣿⣎⠓⡌⢆⠡⢊"),
(12, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠒⢯⣿⣿⣿⣿⣿⣿⣿⣳⢯⣾⣱⣿⡹⢯⣷⣻⠆⣁⠂⠌⠠⠁⢀⠂⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢂⡘⣿⣿⣿⣿⣿⣿⣿⠠⠛⣌⠢⡉⠆"),
(13, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣿⣿⣿⣿⣳⢻⣟⡾⡽⢶⣯⠷⡚⠅⢊⠄⡐⢈⠐⠠⠀⠄⡁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⠠⢘⣿⣿⣿⣿⣿⣿⣿⡌⡱⢀⠣⡘⠄"),
(14, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣿⣿⣿⡿⣼⣻⢾⣽⡿⢏⠆⡰⢁⢊⠀⢂⠐⠠⢈⠐⢈⡐⠠⠌⡀⠐⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⠠⠁⢮⣿⣿⣿⣿⣿⣿⣿⡅⢆⠡⢂⠱⡈"),
(20, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⣿⡹⣬⣳⡽⣿⣿⣿⣿⣷⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⢇⠢⡀⢣⠀⠀⢻⣿⣿⣿⣿⣷⣽⣿⣿⣿⣿⠟⠛⣥⣞⠛⠛⠒⠒⠢⠔⣿⣿⡟⣧⢷⡇⠢⢑⠠⢁⠆"),
(21, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣱⢳⣯⣿⣿⣯⣟⣯⢿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠌⡂⠄⠀⠁⠀⡱⢛⣿⣿⣿⣿⣟⣛⣻⣀⣄⣶⠿⠛⠩⠿⣷⣦⣄⠈⢦⣻⣿⣿⡏⡞⡇⢡⠂⡡⠂⠄"),
(23, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣟⣮⣽⣳⣟⣻⡽⠿⢛⠿⠿⢟⠫⡇⢧⣻⣿⣿⣟⡧⢃⠄⠀⠐⡌⢂⠆⡌⢂⠣⡜⣢⢋⣶⢙⠦⣙⠌⠄⢈⠀⠄⠠⠘⢿⣷⣼⣿⣿⠇⠀⣿⠠⠈⢆⠂⠄"),
(45, r"  |__   __| |  | |   /\   | \ | | |/ / "),
(46, r"     | |  | |__| |  /  \  |  \| | ' / "),
(17, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣻⣿⣿⣿⣽⣿⣿⠿⣟⠿⣿⣿⣿⣿⣿⣿⣷⣿⣯⣾⣵⣳⢎⡵⢢⡕⢤⡹⣴⣛⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⢿⡿⣿⣿⣷⣄⠐⢸⣿⣿⣿⣿⣿⠐⡌⢡⢂⠁⠆"),
(18, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⣿⣿⣏⢿⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣽⣾⡿⠋⠒⠩⢏⣳⢯⣽⣻⣾⣿⣿⣿⣿⢿⠿⣿⣿⣿⣿⣾⣱⣾⠟⠁⠈⣿⣿⣿⣿⣿⡅⡘⢄⠢⢉⠂"),
(10, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⡟⠿⣙⣿⣿⣿⣿⣿⣿⣿⣿⣿⠿⣜⡳⣭⢷⡹⣞⣿⣿⠋⠤⣈⠡⠌⡀⠂⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡐⠸⣽⣿⣿⣿⣿⣿⣿⣧⢏⠦⡙⠤⣉⠆"),
(19, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⣟⣳⣾⣿⣿⣿⣿⡽⣞⣷⣾⣯⣿⣿⣿⣿⣿⣞⣿⠡⠄⠀⠀⢸⣿⣮⣿⣿⣿⣿⣏⣷⣿⣻⣷⡄⣹⣿⣿⣿⣗⣓⣢⣖⡢⣿⣿⣿⠿⡗⡇⡡⠂⡅⠢⢁"),
(22, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⣿⣿⣛⣾⡽⣿⣿⣿⣿⣿⣾⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⡆⠡⠀⠀⠀⠀⢥⢊⡙⢭⠙⡛⢻⢻⠿⡿⢿⡙⠄⠒⠀⠄⠙⠻⣿⣦⢈⢻⣿⣿⠵⡁⡇⢊⠄⢃⠐⡄"),
(47, r"     | |  |  __  | / /\ \ | . ` |  <  "),
(5, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣎⢀⣀⣤⣶⣿⣿⣿⣿⣿⣿⣿⣯⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⢿⠿⣟⠿⣿⣿⢿⣿⣿⣿⣿⣿⣿⣾⣿⣿⣮⣝⡻⣖⢎⡝⢦⡙⣎⠶⣩⠞⣡⢋⡖"),
(6, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⢿⡻⣟⠻⣍⠛⢄⠣⠈⠁⠈⠀⠁⠀⠈⢉⠉⠙⡛⠿⢿⣿⣿⣿⣿⣿⣿⣿⣾⣷⣎⢧⠹⣌⠳⣥⢋⠶⡑⣎"),
(7, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⢿⣿⣿⣿⡻⢜⢣⠃⡄⠃⠀⠄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠓⢄⠀⠀⠓⠺⢿⣿⣿⣿⣿⣿⣿⣿⣻⣎⠳⣌⠻⣤⢋⠖⣩⢒"),
(8, r" ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣿⣻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⢿⣽⢾⣻⣿⡿⠃⡜⠈⠤⠁⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⢢⣀⠀⠐⢈⠻⣿⣿⣿⣿⣿⣿⣯⣿⣦⡙⢦⢍⡚⡔⢫⠜"),
(48, r"     | |  | |  | |/ ____ \| |\  | . \ "),
(29, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⣿⣿⣿⢯⣿⣾⣿⣿⣿⣿⣿⣟⣿⡾⣿⣿⣷⣿⣾⣷⣍⣆⢤⡡⢎⣽⣾⡿⢷⢣⠚⡄⢣⢌⠳⣘⣬⣳⣿⣿⣿⣿⣿⣿⡇⢹⣏⣧⠉⢖⣊⠏⢥⣃⠱⠄⠒⡸"),
(30, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⢻⣿⣿⢿⣿⢿⣿⣿⣿⣷⣿⣷⣟⣯⡷⣿⣯⣿⣿⣿⣿⡾⣝⣻⡿⣟⠹⢢⡃⢧⣘⣥⣾⣷⣿⣿⣿⣿⣿⣿⣿⣿⣿⡷⣸⣿⢯⠐⣣⠞⡾⣔⢢⢡⠘⠠⠁"),
(31, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⢹⡿⣿⡯⣽⣞⣻⡟⣿⣿⣿⣿⣾⣷⣿⣯⣟⣿⣿⣿⣿⣿⠿⣛⣥⣮⣵⣷⣿⣿⣿⢿⠿⣟⣿⣿⣿⡿⣻⢿⣿⣿⡱⢣⡿⣾⡞⢻⢱⢻⡔⢧⡚⣼⠀⡁⠂"),
(24, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣾⣟⡾⣓⠏⡅⡐⠠⢀⠂⡑⢌⢲⡉⣶⣿⡿⣽⣻⢧⢃⠂⠀⠰⡘⢥⡊⡔⠠⣅⡘⣄⢣⠲⡉⠝⠖⣩⠨⡀⢆⣀⣀⠑⢆⠹⣾⣟⡏⠀⠀⢹⠠⠑⢂⠌⡀"),
(25, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣿⣿⡽⣳⣍⠞⢤⡑⠌⡤⢱⡸⣎⣷⣟⡿⣹⣿⢯⢷⡩⠆⠆⠀⢀⠑⢦⡙⢄⠃⡌⢽⢻⢿⣿⣿⣛⡶⣦⣐⡸⣜⠒⣦⢧⠈⡇⢻⠟⠀⠀⠀⢸⠠⣉⠰⢀⠂"),
(26, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣿⣯⡟⣷⢪⡝⣆⢮⡱⣝⣷⣿⣿⣟⣾⡷⣯⢿⣯⠳⣌⠓⡬⢐⠠⢊⢒⡘⡄⢊⡐⣼⠃⢎⡹⣻⢿⣿⣯⣷⣳⣬⣛⠄⢻⣧⢹⢺⠀⠀⠀⠀⡏⠃⢰⠂⢂⠐"),
(28, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢏⣿⣿⣷⢿⣱⢯⣞⣽⣾⣿⣿⣿⢿⣽⣾⣳⡿⣽⣻⣞⡳⠌⠢⡑⢊⠰⢀⡆⣖⣬⣧⠟⡅⠚⡄⠳⢥⣛⢿⡿⣿⣿⣾⣭⢧⠀⣿⣟⡯⣔⣒⣦⠾⣖⠡⠒⡀⠌⣰"),
(37, r" ⣿⣇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⠀⠀⠀⠀⠀⠘⢿⣿⡸⣏⡿⣾⣽⣿⣿⣿⣿⣼⡎⠩⡟⠛⡟⠛⠋⠙⡅⢀⡠⢤⣞⣿⠏⠤⠈⠀⡟⠐⣾⣿⣟⢶⢱⡉⣞⠯⡘⣹⣷⣿⣿⣿⣿⣿⣿⣿"),
(38, r" ⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⡆⠀⢀⣤⣶⣾⣾⣿⣷⣻⣿⢿⣽⣾⡻⢿⣿⣾⣽⣛⡷⣤⠼⣤⢲⣬⢭⡽⣾⡽⣞⡉⠰⠀⠁⢸⠁⢼⡿⠿⠛⣦⡉⠓⢭⡂⠅⣻⣷⣿⣿⣿⣿⣿⣿⣿"),
(43, r" ⣤⣿⣾⣿⣿⡳⣏⣾⣿⣿⣿⣯⢿⣽⣛⡿⣽⣯⢯⡽⢾⣿⣯⣿⣿⣿⣳⣿⡿⣿⣿⣿⣮⢳⡌⡹⡑⢎⠢⠱⣀⠣⡑⢠⠁⠆⡡⣀⢪⣵⣿⡳⣏⡇⠹⣿⣿⣾⣄⠸⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀"),
(50, r"  \ \   / / __ \| |  | | |            "),
(51, r"   \ \_/ / |  | | |  | | |            "),
(39, r" ⣿⣿⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⣷⣾⡿⣏⢷⣿⣟⣿⣿⣷⡿⣟⣾⣷⡿⣧⢺⡝⡻⢟⣻⢛⡻⢛⠭⠾⠋⠋⢑⠊⠄⡐⢁⠂⠀⠀⣰⡟⣼⠃⢆⢸⣷⣄⠀⠉⠲⠿⣿⣿⣿⣿⣿⣿⣿⣿"),
(40, r" ⣿⣿⣧⠀⠀⠀⠀⠀⠀⢀⣠⣤⣶⣿⣿⡿⣿⣿⣥⠿⣜⣯⣿⣯⣿⣿⣿⣿⣿⣻⡾⣽⡟⣧⢻⢭⡳⢤⠳⣘⠣⢎⡥⢃⠜⣀⠊⡔⠈⠄⠀⠠⢁⣾⡿⢡⠘⡠⠂⢿⣏⡛⠒⠤⣀⠀⠉⠙⠛⠿⢿⣿⣿"),
(41, r" ⣿⡿⠋⠀⠀⠀⢤⣶⣾⣿⣿⣟⡿⣯⢷⣻⣿⡿⣎⠿⣼⣿⣿⠷⣿⣿⣿⣿⣿⣿⣻⠽⣿⣭⢳⣎⠱⢎⡱⢡⢛⠬⡒⠍⠒⡀⠃⢠⠈⠀⡴⣳⣿⣿⣌⢣⠘⡀⠁⢾⣿⠀⠀⠀⠀⠉⠒⠦⠄⠀⠀⠈⠙"),
(42, r" ⡵⠶⢶⠿⢿⡿⣟⣾⣿⣽⣿⣾⢿⣯⡷⣻⣽⣿⢎⡟⣶⡿⣭⢿⣿⣿⣿⣟⣿⣿⣯⣟⢴⠛⣧⠎⠳⢌⠱⠌⡸⠠⠑⡈⠁⠄⠂⡀⢀⣶⣴⣿⣙⠻⣿⣷⣥⠀⢁⢺⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀"),
(34, r" ⣇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡇⠀⠘⣾⢽⣿⡴⣻⡜⣯⣿⣿⣿⣷⣄⠀⠀⠀⠘⠀⠀⠀⢸⠀⠀⠀⠀⠀⠀⠀⠀⢈⣶⣿⣟⡎⡤⢉⢲⡟⡰⣾⢳⡟⠠⣁⢎⡵⣭⣯⣣⣏⡜⡡⠂⣰⣾"),
(35, r" ⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡧⠀⠀⠈⢻⣯⣷⡧⢿⡹⣾⣽⣿⣿⣿⣷⣦⣀⣤⠀⠀⠀⠘⠀⠀⠀⢀⣤⣤⣿⣾⢛⣿⣿⣾⠃⡐⢈⣾⠑⣴⣻⡟⡌⡓⢬⣚⣼⣏⣿⣿⣿⣿⣿⣷⣿⣿"),
(36, r" ⣿⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⠀⠀⠀⠀⠙⢿⣿⣎⢿⣳⣯⢿⣿⣿⣿⣿⣿⢿⢿⣷⣶⣿⣿⣿⣿⣿⡏⠹⠀⠉⢏⣾⣽⠣⢀⠐⣸⠃⢼⣯⣿⠼⡰⣍⢷⣺⢍⣞⡷⣿⣿⣿⣿⣿⣿⣿"),
(53, r"     | | | |__| | |__| |_|            "),
(52, r"    \   /| |  | | |  | | |            "),
(44, r"   _______ _    _          _   _ _  __ "),
(32, r" ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⡀⢿⢷⣿⡜⢷⡆⣿⣽⣻⣿⣿⡟⡿⡟⣿⣿⣿⣿⣿⣿⣿⣿⣿⣯⣷⠿⣛⣫⢯⠏⢬⠟⡟⢠⣿⡟⢅⠧⢻⣿⢃⡟⣼⣳⣧⡷⡔⣮⢶⣘⢧⣚⡤⠒⡀⠀"),
(33, r" ⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⡇⠘⣟⣾⣻⡌⣷⡜⣻⣽⣿⣿⣷⡃⠁⠉⡏⠈⢣⠟⠉⠚⢿⠏⠀⠙⡜⠁⠘⠇⠀⠈⠀⣳⣿⡟⢬⡘⢣⣿⢏⣒⣾⣳⡟⠠⢥⠱⣘⢶⢣⠒⡌⡇⠡⠀⢡"),
(49, r"  __ |_| _|_|__|_/_/  _ \_\_| \_|_|\_\ "),
(54, r"     |_|  \____/ \____/(_) ")
]

SCRAMBLED_WHY = [
(1, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(2, r"   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o"),
(3, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(4, r"   o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o"),
(5, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(6, r"   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o"),
(7, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(8, r"   o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o"),
(9, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(10, r"   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o"),
(11, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(12, r"   o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o"),
(13, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(14, r"   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o"),
(27, r"    \ \/  \/ / |  __  | \   /   "),
(28, r"     \  /\  /  | |  | |  | |    "),
(18, r"   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o"),
(19, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(20, r"   o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o"),
(15, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(16, r"   o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o"),
(17, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(24, r"   __          ___    ___     __ "),
(25, r"   \ \        / / |  | \ \   / / "),
(26, r"    \ \  /\  / /| |__| |\ \_/ /  "),
(21, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(38, r"     | | | |__| | |__| |        "),
(39, r"   __|_|  \____/ \____/         "),
(40, r"  |  __ \ / __ \                "),
(31, r"  | \  / | |  | | (___    | |   "),
(43, r"  | |__| | |__| |               "),
(44, r"  |_____/_\____/_ _____  _____  "),
(45, r"  |__   __| |  | |_   _|/ ____| "),
(46, r"     | |  | |__| | | | | (___   "),
(53, r"     | | | |__| |               "),
(32, r"  | |\/| | |  | |\___ \   | |   "),
(22, r"   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o   o | o"),
(23, r"  ---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+--"),
(37, r"    \   /| |  | | |  | |        "),
(33, r"  | |  | | |__| |____) |  | |   "),
(34, r"  |_|  |_|\____/|_____/   |_|   "),
(41, r"  | |  | | |  | |               "),
(42, r"  | |  | | |  | |               "),
(29, r"   __ \/_ \/   |_|__|_| _|_|___ "),
(30, r"  |  \/  | |  | |/ ____|__   __|"),
(54, r"   __|_|_ \____/ ___            "),
(55, r"  |  \/  |  ____|__ \           "),
(56, r"  | \  / | |__     ) |          "),
(47, r"     | |  |  __  | | |  \___ \  "),
(48, r"     | |  | |  | |_| |_ ____) | "),
(35, r"  \ \   / / __ \| |  | |        "),
(36, r"   \ \_/ / |  | | |  | |        "),
(57, r"  | |\/| |  __|   / /           "),
(58, r"  | |  | | |____ |_|            "),
(49, r"   __|_|__|_|__|_|_____|_____/  "),
(50, r"  |__   __/ __ \                "),
(51, r"     | | | |  | |               "),
(52, r"     | | | |  | |               "),
(59, r"  |_|  |_|______|(_)            ")
                               
]

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
