## ADDED Requirements

### Requirement: View generated script in task detail
The task detail page SHALL display the generated podcast script in a readable format after the digestion stage completes.

#### Scenario: Script displayed after digestion
- **WHEN** user views a task that has completed the "digesting" stage
- **THEN** the task detail page SHALL show the full script text with speaker labels

### Requirement: Edit script before TTS
The user SHALL be able to edit the generated script text in a textarea before TTS generation.

#### Scenario: User edits script and saves
- **WHEN** user modifies text in the script editor and clicks "Save Script"
- **THEN** the system SHALL update the script file on disk via `PUT /api/tasks/{id}/script`

### Requirement: Re-generate audio after script edit
After saving an edited script, the user SHALL be able to trigger TTS re-generation without re-running extraction or digestion.

#### Scenario: User triggers TTS re-generation
- **WHEN** user clicks "Re-generate Audio" after saving script changes
- **THEN** the system SHALL re-run TTS and video composition stages with the updated script, skipping extraction and digestion

### Requirement: Script edit endpoint
The backend SHALL provide a `PUT /api/tasks/{id}/script` endpoint that accepts plain text and overwrites the task's script file.

#### Scenario: Script update via API
- **WHEN** a PUT request is sent to `/api/tasks/{id}/script` with a new script body
- **THEN** the system SHALL write the content to the task's `script.txt` file and return 200
