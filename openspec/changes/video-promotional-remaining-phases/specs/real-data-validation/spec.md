## ADDED Requirements

### Requirement: YouTube end-to-end pipeline validation
The system SHALL successfully process a real YouTube video URL through all pipeline stages (extract → digest → TTS → compose) and produce a playable MP4 file.

#### Scenario: Process a real YouTube video with English subtitles
- **WHEN** user submits a YouTube URL that has English auto-generated subtitles
- **THEN** the system extracts subtitles via yt-dlp, generates a podcast script, synthesizes audio via VibeVoice, and renders a 1920x1080 MP4 video

#### Scenario: Handle YouTube video without English subtitles
- **WHEN** user submits a YouTube URL that has no English subtitles available
- **THEN** the system SHALL report a clear error message indicating subtitles are unavailable

### Requirement: EPUB end-to-end pipeline validation
The system SHALL successfully process a real EPUB file through all pipeline stages and produce a playable MP4 file.

#### Scenario: Process a real EPUB book
- **WHEN** user uploads an EPUB file via the web UI
- **THEN** the system extracts chapter text, generates a podcast script covering key themes, synthesizes audio, and renders an MP4 video

### Requirement: PDF end-to-end pipeline validation
The system SHALL successfully process a real PDF file through all pipeline stages and produce a playable MP4 file.

#### Scenario: Process a real PDF document
- **WHEN** user uploads a PDF file via the web UI
- **THEN** the system extracts text, generates a podcast script, synthesizes audio, and renders an MP4 video

### Requirement: AI prompt quality refinement
The AI prompts for summarization and script generation SHALL be refined based on real output quality to produce natural, engaging 2-speaker podcast dialogues.

#### Scenario: Generated script follows VibeVoice format
- **WHEN** the digester generates a podcast script
- **THEN** every line SHALL follow the `Speaker N: text` format and the dialogue SHALL sound natural and conversational with reactions, questions, and varied sentence lengths
