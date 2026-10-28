# Step 2: Read the roof

Input: the building from step 1. Output: **usable roof area (m²)**, an **obstacle list and map**, and a **confidence** level.

It works in two layers:

| Layer | What it finds | How |
|---|---|---|
| LiDAR rules (no AI) | Roof levels, and anything that sticks out: stair housings, lift rooms, tanks, AC units, parapets | Groups 0.5 m cells whose heights differ by at most 0.15 m into surfaces (after Awrangjeb & Fraser 2014). Surfaces of 20 m² or more are usable; everything else is an obstacle |
| AI photo model | What LiDAR misses: skylights, small units, **existing solar panels** | U-Net (ResNet-34 encoder) on the ICGC orthophoto + LiDAR height, 0.1 m pixels, trained on our labelled roofs |

Usable area = free surface, minus 1 m along the roof edge and 0.5 m around each obstacle, keeping only pieces at least 1 m wide and 2 m² in area (room for one panel). With the model, an area is an obstacle if **either** LiDAR or the model says so.

## Setup

From the same folder as step 1, with the `.venv` active:

```
pip install -r requirements-step2.txt
```

PyTorch is large (about 200 MB to 2 GB). You only need it for the AI part. Train on Google Colab if your laptop has no NVIDIA GPU, or if pip finds no PyTorch for your Python version.

## 1. LiDAR only (works now)

Download the ICGC LiDAR tile (LAZ) that covers the building into `data\lidar`, then:

```
python -m step2.read_roof "Carrer de Roger de Llúria 56, Barcelona" --laz-dir data\lidar --out lluria56
```

Outputs:

- `lluria56.json`: the numbers
- `lluria56_preview.png`: photo with green = free, red = obstacle, white = roof outline, yellow = where panels fit
- `lluria56_photo.png`: the plain roof photo
- `lluria56_height.png`: height above the main roof, green = roof level, yellow = about 1 to 2.5 m (parapets, AC units), orange to red = 2.5 m and up (stair housings, lift rooms), blue = lower than the main roof
- `lluria56_usable.geojson`: the usable area, to drag into QGIS

## 2. Make training chips and label them

1. Make `roofs.csv` with columns `address,block` (block = 1 to 9 from the focus-area diagram), about 20 roofs per block.
2. `python -m step2.dataset roofs.csv --laz-dir data\lidar --out data\chips`
3. For each roof, copy `data\chips\<id>_prelabel.png` to `data\labels\<id>_label.png`. Open it as a layer over `<id>_rgb.png` in GIMP or Photoshop and correct it with a **hard pencil** (no anti-aliasing) in these exact colours:

| Class | RGB |
|---|---|
| Outside the roof | 0, 0, 0 |
| Free roof | 60, 180, 75 |
| Obstacle (incl. skylights) | 230, 25, 75 |
| Existing solar panel | 0, 130, 200 |
| Not sure (ignored) | 255, 255, 255 |

The LiDAR pre-label already has the big obstacles, so most of the work is adding skylights, small units and existing panels.

## 3. Train

```
python -m step2.train --chips data\chips --labels data\labels --val-blocks 7 --test-blocks 8 9
```

Training splits by block, so test roofs are never next door to training roofs. It saves `models\roof_unet.pt` and the test scores (IoU per class) in `models\roof_unet_metrics.json`.

## 4. Use the model

```
python -m step2.read_roof "Carrer de Roger de Llúria 56, Barcelona" --laz-dir data\lidar --model models\roof_unet.pt --out lluria56
```

The JSON then also has `existing_panels_m2`, `seen_only_by_ai_m2` (what the AI added over LiDAR), `model_certainty`, and `confidence` (high, medium or low).

## Known gaps: check these first

- **Orthophoto service not yet tested live.** It was written without access to ICGC from the workspace. If the photo fails, run `python -m step2.ortho --layers` and set `ORTHO_LAYER` in `step2/ortho.py` to the current colour orthophoto layer.
- **Thresholds are starting values** (top of `step2/lidar_roof.py`). Tune them on the 9 blocks.
- **RID and BDAPPV are not wired in yet.** They can be added as extra training data once our own labels work.

## Files

```
step2/grid.py        shared 0.5 m / 0.1 m grid around a building
step2/lidar_roof.py  LiDAR rules: surfaces, obstacles, usable area
step2/ortho.py       ICGC orthophoto download (WMS)
step2/dataset.py     training chips + LiDAR pre-labels
step2/model.py       U-Net, classes and colours, prediction
step2/train.py       training with a block split, IoU scores
step2/read_roof.py   the step 2 command: address -> roof reading
tests/test_step2_*.py offline tests
```
