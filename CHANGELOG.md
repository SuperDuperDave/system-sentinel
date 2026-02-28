# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2025-01-15

### Added
- WHEA storm detection and visualization
- Deep diagnostics with hardware domain analysis (memory, PCIe, power, forensic signals)
- Hardware topology tree with component-level views (CPU, GPU, storage, network, motherboard)
- System constraint analysis
- Context Composer for AI-augmented diagnostics
- Prompt Gallery with 6 specialist diagnostic modes
- Live event streaming via SSE

### Changed
- Expanded backend service architecture with domain-specific analyzers
- Improved frontend dashboard with tabbed deep diagnostics views

## [0.1.0] - 2024-12-01

### Added
- Initial release
- FastAPI backend with system event collection
- WHEA error log collection and decoding
- Crash dump collection
- System info snapshots
- Next.js frontend dashboard
- Event list with filtering
- Capture pack generation (ZIP export)
- Driver change tracking
