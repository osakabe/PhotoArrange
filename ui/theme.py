def get_style_sheet():
    return """
    QMainWindow, QDialog {
        background-color: #0F111A;
    }
    
    QWidget {
        background-color: #0F111A;
        color: #E2E4EB;
        font-family: 'Segoe UI', 'Inter', 'Roboto', sans-serif;
        font-size: 13px;
    }
    
    /* Header & Toolbar */
    #header {
        background-color: #1A1D2E;
        border-bottom: 1px solid #2D324A;
        padding: 4px 15px;
        min-height: 50px;
        max-height: 60px;
    }
    
    QPushButton {
        background-color: #2D324A;
        border: 1px solid #3F4663;
        border-radius: 6px;
        padding: 6px 12px;
        color: #E2E4EB;
        font-weight: 600;
    }
    
    QPushButton:hover {
        background-color: #3F4663;
        border-color: #565E81;
    }
    
    QPushButton:pressed {
        background-color: #24283D;
    }
    
    QPushButton#primary {
        background-color: #3D5AFE;
        border: none;
        color: white;
    }
    
    QPushButton#primary:hover {
        background-color: #536DFE;
    }
    
    QPushButton#danger {
        background-color: #FF5252;
        border: none;
        color: white;
    }

    /* Sidebar / TreeView */
    QTreeView {
        background-color: #141724;
        border: none;
        border-right: 1px solid #2D324A;
        outline: none;
        padding: 5px;
    }
    
    QTreeView::item {
        padding: 6px;
        border-radius: 4px;
        margin: 2px 0;
    }
    
    QTreeView::item:hover {
        background-color: #1F2336;
    }
    
    QTreeView::item:selected {
        background-color: #2D324A;
        color: #3D5AFE;
    }

    /* Align items without children by adding left margin if needed, 
       but usually indentation is handled by the model. 
       We'll ensure the text starts at the same spot. */
    
    QHeaderView::section {
        background-color: #1A1D2E;
        color: #8A8EA8;
        padding: 8px;
        border: none;
        font-weight: bold;
        text-transform: uppercase;
        font-size: 11px;
    }

    /* Content Area / GridView */
    QScrollArea {
        background-color: #0F111A;
        border: none;
    }
    
    #thumbnail_container {
        background-color: #0F111A;
    }
    
    /* Section Frame */
    QFrame#section {
        background-color: #1A1D2E;
        border: 1px solid #2D324A;
        border-radius: 8px;
    }
    
    /* Progress Bar */
    QProgressBar {
        border: 1px solid #2D324A;
        border-radius: 4px;
        background-color: #1A1D2E;
        text-align: center;
        height: 8px;
        font-size: 0px; /* Hide text to make it cleaner */
    }
    
    QProgressBar::chunk {
        background-color: #3D5AFE;
        border-radius: 3px;
    }
    
    /* Slider */
    QSlider::groove:horizontal {
        border: 1px solid #2D324A;
        height: 6px;
        background: #1A1D2E;
        margin: 2px 0;
        border-radius: 3px;
    }
    
    QSlider::handle:horizontal {
        background: #3D5AFE;
        border: 1px solid #3D5AFE;
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }

    /* Splitter */
    QSplitter::handle {
        background-color: #2D324A;
    }
    """
