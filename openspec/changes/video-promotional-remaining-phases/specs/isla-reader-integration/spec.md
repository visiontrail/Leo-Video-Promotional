## ADDED Requirements

### Requirement: Isla-Reader curated highlights mode for EPUB
The system SHALL support an optional "Curated Highlights" mode for EPUB sources that uses the Isla-Reader Promotion-Agent pipeline to extract AI-curated book highlights before script generation.

#### Scenario: User enables curated highlights mode
- **WHEN** user uploads an EPUB and selects "Curated Highlights" processing mode
- **THEN** the system SHALL invoke `generate-promotion.sh --epub <path> --style none` to get curated highlights, then use those highlights as input for the AI digester

#### Scenario: Isla-Reader CLI not available
- **WHEN** the system attempts to invoke the Isla-Reader Swift CLI but it is not installed
- **THEN** the system SHALL fall back to the default Python ebooklib extraction with a warning message

### Requirement: Curated highlights as podcast talking points
When curated highlights mode is used, the AI digester SHALL receive the curated highlights (from `selected.stage2.json`) as pre-selected talking points rather than raw chapter text.

#### Scenario: Highlights fed to digester
- **WHEN** Isla-Reader produces 30 curated highlights from a book
- **THEN** the digester SHALL use those highlights as the primary source material, generating a podcast script that discusses the most impactful quotes and insights

### Requirement: Processing mode selector in UI
The EPUB tab in the task creation form SHALL include a processing mode selector: "Full Text" (default) or "Curated Highlights".

#### Scenario: Mode selector displayed for EPUB source
- **WHEN** user selects EPUB source type in the task form
- **THEN** a processing mode selector SHALL appear with "Full Text" and "Curated Highlights" options
