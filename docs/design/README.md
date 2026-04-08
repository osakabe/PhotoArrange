# PhotoArrange Design Documentation (v4.5 Explosive Speed)

This directory contains the formal architectural specifications and diagrams for the PhotoArrange project.

## 🚀 Architectural Vision: Explosive Speed
The current architecture (v4.5) is focused on overcoming the "N+1 Problem" and "JOIN latency" inherent in large media databases. We achieve **sub-100ms loading** for up to 100,000 records through:
1.  **Denormalized-Local Search**: Mirroring critical metadata into secondary tables.
2.  **Explosive Sorting Indices**: Using composite B-Tree indexes for single-table filtered sorting.
3.  **Decoupled Initialization**: Using `DatabaseMigrationManager` to handle heavy lifecycle tasks asynchronously.

## 📂 Document Map
- [**Specification.md**](specification.md): Technical requirements and engineering standards (Complexity <= 10).
- [**ER_Diagram.md**](er_diagram.md): Denormalized schema and explosive index definitions.
- [**Class_Diagram.md**](class_diagram.md): Component structure and selection-predicate strategies.
- [**Sequence_Diagram.md**](sequence_diagram.md): Decoupled startup and JOIN-free loading flows.
- [**State_Machine.md**](state_machine.md): AI Suggestion lifecycle and interaction states.

---
*Maintained by the QA Sheriff. Last Major Update: v4.5 Reform.*
