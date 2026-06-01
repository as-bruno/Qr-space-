# 🖼️ Smart Image Compressor — Free Adaptive WebP Batch Optimizer

> **The same compression engine powering [WallpepherSphere](https://wallpepersphere.com/) — one of the web's fastest-loading wallpaper sites.**

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![WebP](https://img.shields.io/badge/Output-WebP-orange)](https://developers.google.com/speed/webp)
[![Compression](https://img.shields.io/badge/Compression-Adaptive-purple)]()

A **free, open-source batch image compressor** that uses adaptive quality logic to shrink JPG, PNG, BMP, TIFF, and WebP images to under 1MB — without destroying quality. Drop files in, get optimized WebP images out. No cloud. No uploads. Runs 100% locally on your machine.

---

## ✨ Why This Tool Exists

Most image compressors apply the same quality setting to every image. That's a bad idea. A solid-color wallpaper and a detailed photograph need completely different treatment.

This tool analyzes each image **individually** before compressing it:
- A simple gradient background? → Aggressive compression, tiny file, no visible loss.
- A detailed landscape photo? → Gentle compression, preserves every pixel of detail.

The result: **smaller files, better quality, zero guesswork.**

---

## 🖼️ Example Results

Real wallpapers processed by this tool, live on [WallpepherSphere](https://wallpepersphere.com/):

| Preview | Resolution | Compressed To | Savings |
|---|---|---|---|
| [![Anya Forger Park Bench](https://pub-ed3ba9c57ea34b499ee04ab55aba6b97.r2.dev/medium_1240551-1780190908.webp)](https://wallpepersphere.com/view/image/5398/anya-forger-park-bench) | 5760×3240px · 4K | < 1MB WebP | **~88%** |
| [![Anya Forger Cold Winter](https://pub-ed3ba9c57ea34b499ee04ab55aba6b97.r2.dev/medium_1292358-1780190893.webp)](https://wallpepersphere.com/view/image/5397/anya-forger-cold-winter) | 5760×3240px · 4K | < 1MB WebP | **~88%** |

> Click any image to view it on [wallpepersphere.com](https://wallpepersphere.com/) — every wallpaper on the site was compressed with this tool.

---

## 🚀 Features

| Feature | Description |
|---|---|
| **Adaptive Quality** | Each image gets a custom quality score (0–95) based on its complexity |
| **Complexity Analysis** | Edge detection + color variance scoring via NumPy |
| **Batch Processing** | Compress entire folders at once |
| **Parallel Processing** | Uses all CPU cores simultaneously for speed |
| **Smart Skip** | Files already under 1MB are copied untouched |
| **4K Resize** | Images wider than 3840px are auto-resized (configurable) |
| **WebP Output** | Modern, web-optimized format — 25–35% smaller than JPEG |
| **Binary Search** | Fine-tunes quality per image to hit the exact target size |
| **Zero Config** | Works out of the box with sensible defaults |
| **100% Local** | Your images never leave your computer |

---

## 📦 Installation

**Requirements:** Python 3.8+

```bash
# Clone the repository
git clone https://github.com/yourusername/smart-image-compressor.git
cd smart-image-compressor

# Install dependencies
pip install Pillow numpy
```

That's it. No Docker, no complex setup.

---

## 🔧 Usage

### Basic (Recommended)

1. Place your images inside the `input/` folder
2. Run the script:

```bash
python app.py
```

3. Find your compressed WebP files in the `output/` folder

### First Run

If `input/` doesn't exist yet, the script creates it and tells you:

```
[*] Created input folder: /path/to/input
[*] Please place your images inside the 'input' folder and run the script again.
```

### Example Output

```
======================================================================
[*] Found 4 images. Starting adaptive compression...
[*] Target size: 1.0MB | Max width: 3840px
======================================================================

[*] Analyzing 'wallpaper_4k.jpg'...
    Original: 3840x2160px | Size: 8.42MB
    Complexity Score: 84.3% (0%=simple, 100%=complex detail)
    → Compressing with adaptive target quality: 92/100...
[+] Success!
    Final Size: 0.94 MB (saved 88.8%)
    Quality Setting: 92/100

[*] Analyzing 'gradient_bg.png'...
    Original: 1920x1080px | Size: 2.14MB
    Complexity Score: 12.7% (0%=simple, 100%=complex detail)
    → Compressing with adaptive target quality: 67/100...
[+] Success!
    Final Size: 0.31 MB (saved 85.5%)
    Quality Setting: 67/100

[✓] 'icon_small.png' is 0.22MB (already under 1.0MB). Copying without compression...
```

---

## ⚙️ Configuration

Open `app.py` and edit the settings at the top of `if __name__ == "__main__"`:

```python
# --- SETTINGS ---
TARGET_SIZE_MB = 1.0   # Target file size. Files under this are skipped.
MAX_WIDTH = 3840       # Max image width in pixels (3840 = 4K). Set to 0 to disable resizing.
# ----------------
```

---

## 🧠 How the Adaptive Quality Algorithm Works

This is the core innovation. Most tools compress everything at Q75 or Q80 and call it a day. This tool does something smarter.

### Step 1 — Complexity Scoring

Every image is scored on a **0.0 to 1.0 complexity scale** before compression:

```
complexity = (edge_score × 0.7) + (color_variance_score × 0.3)
```

- **Edge score**: Measures average pixel difference across horizontal and vertical axes — a proxy for fine detail. High variance = lots of edges = complex image.
- **Color score**: Measures standard deviation across sampled RGB pixels.

### Step 2 — Adaptive Quality Calculation

| File Size | Base Quality | Max Adjustment | Resulting Range |
|-----------|-------------|----------------|-----------------|
| < 2MB | 65 | +18 | 65–83 |
| 2–5MB | 78 | +18 | 78–96 → capped at 95 |
| > 5MB | 85 | +18 | 85–95 |

A highly complex 6MB image gets Q95. A simple 1.5MB gradient gets Q65. Both end up under 1MB. Neither looks bad.

### Step 3 — Binary Search Fine-Tuning

After the quality target is calculated, a binary search runs within a ±10 range to hit the target size as precisely as possible. If that window fails, it expands to the full Q20–Q95 range.

---

## 🌐 See It In Production

This algorithm runs live on **[WallpepherSphere](https://wallpepersphere.com/)** — a high-performance wallpaper website built for speed and SEO.

Every wallpaper served on the site has been processed through this exact pipeline:
- ✅ Compressed to under 1MB
- ✅ Served as WebP
- ✅ Adaptive quality per image — detailed photos preserve their sharpness

Want to generate your own wallpapers? Try the free AI wallpaper generator:
👉 **[wallpepersphere.com/generator](https://wallpepersphere.com/generator)**

Browse 10,000+ free wallpapers optimized with this tool:
👉 **[wallpepersphere.com](https://wallpepersphere.com/)**

---

## 📁 Project Structure

```
smart-image-compressor/
├── app.py          # Main script — all logic lives here
├── input/          # Drop your images here (auto-created)
├── output/         # Compressed WebP files appear here (auto-created)
├── README.md
└── LICENSE
```

---

## 🆚 Comparison

| Tool | Adaptive Quality | Batch | Local | Free | WebP |
|------|:---:|:---:|:---:|:---:|:---:|
| **This tool** | ✅ | ✅ | ✅ | ✅ | ✅ |
| Squoosh | ❌ | ❌ | ✅ | ✅ | ✅ |
| TinyPNG | ❌ | Limited | ❌ | Limited | ❌ |
| ImageMagick | ❌ | ✅ | ✅ | ✅ | ✅ |
| Compressor.io | ❌ | ❌ | ❌ | Limited | ✅ |

---

## 🔄 Supported Input Formats

| Format | Extension |
|--------|-----------|
| JPEG | `.jpg`, `.jpeg` |
| PNG | `.png` |
| BMP | `.bmp` |
| TIFF | `.tiff` |
| WebP | `.webp` |

All formats output as **WebP** — the best format for web delivery.

---

## 💡 Use Cases

- **Web developers** — Optimize images before deploying to a site
- **Bloggers & content creators** — Reduce image size for faster page loads
- **Wallpaper sites** — Batch compress entire libraries (exactly what WallpepherSphere does)
- **SEO optimization** — Smaller images = faster Core Web Vitals = better rankings
- **Social media** — Hit platform size limits without visible quality loss
- **DaVinci Resolve / Video editors** — Reduce source asset sizes before import

---

## 📋 Requirements

```
Pillow>=9.0.0
numpy>=1.21.0
```

Python standard library only beyond that (`os`, `sys`, `io`, `concurrent.futures`).

---

## 🤝 Contributing

PRs welcome. If you find a case where the complexity scoring gets it wrong (compresses too aggressively or not enough), open an issue with the image characteristics and the output you got.

Ideas for future improvements:
- [ ] CLI flags (`--target`, `--width`, `--input`, `--output`)
- [ ] Per-format output option (keep JPEG for some cases)
- [ ] GUI wrapper
- [ ] Progress bar with `tqdm`
- [ ] Metadata stripping option

---

## 📄 License

MIT — free to use, modify, and distribute. Attribution appreciated but not required.

---

## 🔗 Links

- 🌐 **Main site**: [wallpepersphere.com](https://wallpepersphere.com/)
- 🎨 **AI Wallpaper Generator**: [wallpepersphere.com/generator](https://wallpepersphere.com/generator)
- 📺 **YouTube** *(AI & web dev tutorials)*: coming soon

---

*Built by the team behind [WallpepherSphere](https://wallpepersphere.com/) — if this tool saved you time, go grab a free wallpaper.*
