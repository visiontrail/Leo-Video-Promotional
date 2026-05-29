## ADDED Requirements

### Requirement: Audio playback in task detail
The task detail page SHALL include an audio player that allows the user to listen to the TTS-generated WAV file after the TTS stage completes.

#### Scenario: Audio player displayed after TTS
- **WHEN** user views a task that has completed the "tts" stage
- **THEN** the task detail page SHALL display an HTML audio player with playback controls for the generated WAV file

### Requirement: Audio preview before video render
The user SHALL be able to listen to the generated audio and decide whether to proceed with video rendering or go back to edit the script.

#### Scenario: User approves audio and proceeds to render
- **WHEN** user listens to the audio preview and clicks "Render Video"
- **THEN** the system SHALL proceed to the video composition stage

#### Scenario: User rejects audio and edits script
- **WHEN** user listens to the audio preview and decides to edit the script
- **THEN** the user SHALL be able to navigate to the script editor, make changes, and re-generate audio
