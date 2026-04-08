# State Machine Diagrams (v4.5 Explosive Speed)

## 1. Media Processing & AI Lifecycle
Defines the backend pipeline from file discovery to person registration.

```mermaid
stateDiagram-v2
    [*] --> Discovered: File System Scan
    Discovered --> Indexed: Metadata Extraction
    Indexed --> FeaturesExtracted: DINOv2 Embedding
    FeaturesExtracted --> FacesDetected: InsightFace Analysis (ResNet/ArcFace)
    
    state FacesDetected {
        [*] --> Unknown: Unclassified
        Unknown --> Suggested: AI Similarity Search (FAISS)
        Suggested --> Registered: Manual User Label
        Unknown --> Registered: Manual User Label
        Registered --> Ignored: User Action
        Unknown --> Ignored: User Action
        
        note right of Suggested
            GPU Memory Management:
            Call torch.cuda.empty_cache() after
            every 500-batch processing or on
            worker stop/cancel to prevent OOM.
        end note
    }
    
    Indexed --> [*]: File Deleted
    FacesDetected --> [*]: File Deleted / Data Reset
```

## 2. Face Manager UI Interaction States (NEW v4.5)
Defines how the UI transitions between viewing and suggestion modes.

```mermaid
stateDiagram-v2
    [*] --> Idle: Application Start
    
    state LibraryMode {
        Idle --> LoadingFaces: select_category()
        LoadingFaces --> Displaying: data_ready (<100ms)
        Displaying --> Idle: completion
    }

    state SuggestionMode {
        Displaying --> CalculatingSimilarities: toggle_suggestion(ON)
        CalculatingSimilarities --> BuildingUI: suggestions_ready
        BuildingUI --> AsyncRendering: append_data()
        AsyncRendering --> DisplayingSuggestions: crops_ready
        DisplayingSuggestions --> CalculatingSimilarities: change_threshold()
    }

    SuggestionMode --> LibraryMode: toggle_suggestion(OFF)
    LibraryMode --> [*]: Close App
```
