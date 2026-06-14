import os
import sys
from PIL import Image
import io
import concurrent.futures
import numpy as np

def analyze_image_complexity(img):
    """
    Analyzes image complexity to determine compression aggressiveness.
    Returns a score 0-1 (0=simple, 1=complex).
    
    - Simple images (solid colors, gradients): low score = can compress more aggressively
    - Complex images (photos, fine details): high score = needs gentler compression
    """
    try:
        # Convert to grayscale for analysis
        gray = img.convert('L')
        pixels = np.array(gray)
        
        # Calculate edge detection (Laplacian variance) to measure detail
        # More edges = more detail = less aggressive compression
        if len(pixels.shape) == 2 and pixels.shape[0] > 2 and pixels.shape[1] > 2:
            # Simple edge detection: variance of differences
            vertical_diff = np.abs(np.diff(pixels, axis=0)).mean()
            horizontal_diff = np.abs(np.diff(pixels, axis=1)).mean()
            edge_score = (vertical_diff + horizontal_diff) / 255.0
        else:
            edge_score = 0.5
        
        # Calculate color diversity (if not grayscale)
        rgb_img = img.convert('RGB')
        rgb_array = np.array(rgb_img)
        if len(rgb_array.shape) == 3:
            # Sample pixels to avoid processing huge images
            sample = rgb_array[::max(1, rgb_array.shape[0]//100), ::max(1, rgb_array.shape[1]//100)]
            color_variance = np.std(sample)
            color_score = min(color_variance / 100.0, 1.0)
        else:
            color_score = 0.5
        
        # Combine scores: prioritize edge detection (detail is more important than color diversity)
        complexity_score = (edge_score * 0.7) + (color_score * 0.3)
        return min(complexity_score, 1.0)
    except Exception as e:
        print(f"    [!] Could not analyze complexity: {e}. Using default.")
        return 0.5

def calculate_adaptive_quality(file_size_mb, complexity_score):
    """
    Calculates target quality based on file size and image complexity.
    
    Returns None if file is already under 1MB (skip compression).
    
    Logic:
    - Small files (< 2MB) with low complexity: aggressive compression (lower quality ok)
    - Large files (> 5MB) with high complexity: gentle compression (higher quality)
    - Medium files: balanced approach
    - High complexity images: boost quality to preserve detail
    """
    # Return None if already under 1MB - no compression needed
    if file_size_mb < 1.0:
        return None
    
    # Base quality ranges based on file size
    if file_size_mb < 2.0:
        # Small files: can afford more aggressive compression
        base_quality = 65
    elif file_size_mb < 5.0:
        # Medium files: balanced compression
        base_quality = 78
    else:
        # Large files: gentle compression to preserve quality
        base_quality = 85
    
    # Adjust based on complexity: high complexity = higher quality to preserve detail
    # complexity_score: 0 (simple/low detail) to 1 (complex/high detail)
    quality_adjustment = complexity_score * 18  # Up to +18 points for complex images
    target_quality = int(base_quality + quality_adjustment)
    
    return min(target_quality, 95)  # Cap at 95 to ensure some compression

def compress_image(input_path, output_path, target_size_mb=1.0, max_width=3840):
    """
    Intelligently compresses an image using adaptive quality based on file size and complexity.
    
    Key features:
    - Files already under 1MB are copied without compression
    - Each file is analyzed for detail level (complexity)
    - Quality setting is calculated per-image, not globally
    - Large files with high detail get gentler compression
    - Small files with low detail get aggressive compression
    """
    try:
        # Check original file size
        original_size_mb = os.path.getsize(input_path) / (1024 * 1024)
        filename = os.path.basename(input_path)
        
        # If already under 1MB, just copy it to output in WebP format
        if original_size_mb < target_size_mb:
            print(f"[✓] '{filename}' is {original_size_mb:.2f}MB (already under {target_size_mb}MB). Copying without compression...")
            with open(input_path, 'rb') as src:
                with open(output_path, 'wb') as dst:
                    dst.write(src.read())
            print(f"    Final Size: {original_size_mb:.2f} MB\n")
            return
        
        img = Image.open(input_path)
        original_width = img.width
        original_height = img.height
        
        # Analyze image complexity BEFORE compression
        print(f"[*] Analyzing '{filename}'...")
        complexity_score = analyze_image_complexity(img)
        print(f"    Original: {original_width}x{original_height}px | Size: {original_size_mb:.2f}MB")
        print(f"    Complexity Score: {complexity_score:.1%} (0%=simple, 100%=complex detail)")
        
        # Calculate adaptive quality target based on file size + complexity
        target_quality = calculate_adaptive_quality(original_size_mb, complexity_score)
        
        if target_quality is None:
            # Already under 1MB, copy it
            print(f"    → File already under {target_size_mb}MB. Copying as-is.\n")
            with open(input_path, 'rb') as src:
                with open(output_path, 'wb') as dst:
                    dst.write(src.read())
            return
        
        # 1. Resolution Check
        # If image is wider than 4K, resize it to 4K width
        if img.width > max_width:
            print(f"    → Resizing from {img.width}px to {max_width}px width (preserves density)...")
            ratio = max_width / float(img.width)
            new_height = int((float(img.height) * float(ratio)))
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            print(f"      New size: {img.width}x{img.height}px")
        
        # 2. Adaptive Compression using target quality
        target_bytes = target_size_mb * 1024 * 1024
        min_quality = 20  # Don't go below 20 to preserve minimum acceptable quality
        max_quality = 95
        best_quality = target_quality
        best_data = None
        
        print(f"    → Compressing with adaptive target quality: {target_quality}/100...")
        
        # Start binary search around the calculated quality
        low = max(min_quality, target_quality - 10)
        high = min(max_quality, target_quality + 10)
        
        while low <= high:
            mid = (low + high) // 2
            img_byte_arr = io.BytesIO()
            
            # method=6 = best quality/size ratio for WebP
            img.save(img_byte_arr, format='WEBP', quality=mid, method=6)
            size = img_byte_arr.tell()
            
            if size <= target_bytes:
                best_quality = mid
                best_data = img_byte_arr.getvalue()
                low = mid + 1  # Try for better quality
            else:
                high = mid - 1  # File too big, lower quality
        
        # If binary search didn't find a solution, expand search to full range
        if best_data is None:
            print(f"      Expanding search range (adaptive quality was too strict)...")
            low = min_quality
            high = max_quality
            
            while low <= high:
                mid = (low + high) // 2
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='WEBP', quality=mid, method=6)
                size = img_byte_arr.tell()
                
                if size <= target_bytes:
                    best_quality = mid
                    best_data = img_byte_arr.getvalue()
                    low = mid + 1
                else:
                    high = mid - 1
        
        if best_data:
            with open(output_path, 'wb') as f:
                f.write(best_data)
            
            final_size_mb = len(best_data) / (1024 * 1024)
            compression_ratio = (1 - final_size_mb / original_size_mb) * 100
            
            print(f"[+] Success!")
            print(f"    Final Size: {final_size_mb:.2f} MB (saved {compression_ratio:.1f}%)")
            print(f"    Quality Setting: {best_quality}/100\n")
        else:
            print(f"[-] Could not compress to {target_size_mb}MB at quality {min_quality}/100.")
            print(f"    Image has too much detail. Saving at lowest quality ({min_quality})...")
            # Fallback: Save at lowest quality
            img.save(output_path, format='WEBP', quality=min_quality, method=6)
            fallback_size = os.path.getsize(output_path) / (1024 * 1024)
            print(f"    Final Size: {fallback_size:.2f} MB\n")
    
    except Exception as e:
        print(f"[!] Error processing '{os.path.basename(input_path)}': {e}\n")

if __name__ == "__main__":
    # ── Standalone usage ──────────────────────────────────────────────────────
    # Place images in the 'input/' folder next to this script, then run:
    #   python compressor.py
    # Compressed WebP files are written to 'output/'.
    # ─────────────────────────────────────────────────────────────────────────

    # --- SETTINGS ---
    TARGET_SIZE_MB = 1.0    # Files under this size are copied as-is
    MAX_WIDTH      = 3840   # Resize to 4K if wider than this
    # ----------------

    base_dir   = os.path.dirname(os.path.abspath(__file__))
    input_dir  = os.path.join(base_dir, "input")
    output_dir = os.path.join(base_dir, "output")

    if not os.path.exists(input_dir):
        os.makedirs(input_dir)
        print(f"[*] Created input folder: {input_dir}")
        print("[*] Place your images inside 'input/' and run again.")
        sys.exit(0)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"[*] Created output folder: {output_dir}")

    image_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp')
    images_found = [
        f for f in os.listdir(input_dir)
        if f.lower().endswith(image_extensions)
    ]

    if not images_found:
        print(f"[-] No images found in '{input_dir}'.")
        sys.exit(0)

    print(f"\n{'='*70}")
    print(f"[*] Found {len(images_found)} images. Starting adaptive compression...")
    print(f"[*] Target: {TARGET_SIZE_MB} MB  |  Max width: {MAX_WIDTH} px")
    print(f"{'='*70}\n")

    max_workers = os.cpu_count() or 1

    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for filename in images_found:
            input_path = os.path.join(input_dir, filename)

            # Strip ALL extensions so 'photo.jpeg' and 'photo.webp' both become
            # 'photo_compressed.webp' — not 'photo.jpeg_compressed.webp'
            name_only = filename
            while True:
                root, ext = os.path.splitext(name_only)
                if not ext:
                    break
                name_only = root

            output_path = os.path.join(output_dir, f"{name_only}_compressed.webp")
            futures.append(
                executor.submit(compress_image, input_path, output_path,
                                TARGET_SIZE_MB, MAX_WIDTH)
            )
        concurrent.futures.wait(futures)

    print(f"{'='*70}")
    print(f"[=] All done! Check the 'output/' folder.")
    print(f"{'='*70}")