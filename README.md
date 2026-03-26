# PhotoArrange

PhotoArrange is a powerful local photo and video organizer that uses AI to automatically categorize your media. It features face recognition, duplicate detection, and geographical mapping.

## Key Features

- **Face Recognition**: Automatically detects and clusters faces using `insightface`.
- **Infinite Scrolling**: Smoothly browse thousands of media files with automatic pagination.
- **Lazy Loading**: High-performance sidebar and grid for large datasets (16,000+ files).
- **Duplicate Detection**: Identifies potential duplicates using image hashing.
- **Geocoding**: Maps photo locations to cities and prefectures using GeoNames data.

## Requirements

The application requires Python 3.10+ and several numerical libraries. It is recommended to use a Conda environment (e.g., Miniforge).

Dependencies:
- PySide6
- insightface
- onnxruntime
- scikit-learn
- numpy
- opencv-python
- imagehash
- scipy

## Installation

1. Clone the repository:
   ```bash
   git clone <repository_url>
   cd PhotoArrange
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Application

To launch the application:
```bash
python main.py
```

## Project Structure

- `main.py`: Entry point and main UI controller.
- `core/`: Database and utility logic.
- `processor/`: AI processing modules (Face, Image, Geo).
- `ui/`: Custom widgets, dialogs, and styling.
