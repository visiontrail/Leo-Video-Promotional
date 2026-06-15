## ADDED Requirements

### Requirement: Selectable TTS model
The system SHALL support selecting which local VibeVoice TTS model to use for audio synthesis from a fixed set of installed models (1.5B and 0.5B Realtime).

The selectable models are:
- `vibevoice-1.5b` — higher quality, slower (env `env_vibevoice_1.5b.sh`, project `VibeVoice-1.5B`, inference script `demo/inference_from_file.py`, `--speaker_names` plural). This is the default.
- `vibevoice-0.5b` — faster "draft" quality, realtime model (env `env_vibevoice.sh`, project `VibeVoice`, inference script `demo/realtime_model_inference_from_file.py`, `--speaker_name` singular).

#### Scenario: User selects the 1.5B model
- **WHEN** user creates a task and selects the "1.5B (high quality)" TTS model
- **THEN** the pipeline SHALL invoke the VibeVoice 1.5B environment and inference script for audio synthesis

#### Scenario: User selects the 0.5B draft model
- **WHEN** user creates a task and selects the "0.5B (fast draft)" TTS model
- **THEN** the pipeline SHALL invoke the VibeVoice 0.5B Realtime environment and inference script for audio synthesis

#### Scenario: Default model used when none selected
- **WHEN** user creates a task without selecting a TTS model
- **THEN** the system SHALL use the default model `vibevoice-1.5b`

### Requirement: TTS model registry and invocation
The TTS subprocess wrapper SHALL resolve the correct environment script, project directory, inference script path, and speaker-argument flag based on the selected model key, rather than hard-coding a single model.

#### Scenario: Model-specific invocation parameters
- **WHEN** the pipeline runs TTS for a selected model key
- **THEN** the wrapper SHALL source that model's env script, `cd` into that model's project directory, and call that model's inference script with the correct speaker flag (`--speaker_names` for 1.5B, `--speaker_name` for 0.5B)

#### Scenario: Unknown model key rejected
- **WHEN** a task specifies a TTS model key that is not in the registry
- **THEN** the system SHALL reject it with a clear error rather than silently falling back

### Requirement: TTS model selection in task creation
The task creation form SHALL include a dropdown to select the TTS model, defaulting to the 1.5B model, with the 0.5B model labeled as a faster draft option.

#### Scenario: TTS model dropdown displayed
- **WHEN** user opens the task creation form
- **THEN** the form SHALL display a TTS model selector with options "1.5B (high quality)" and "0.5B (fast draft)", defaulting to 1.5B

#### Scenario: Selected model persisted on the task
- **WHEN** user submits a task with a chosen TTS model
- **THEN** the chosen model key SHALL be stored on the task and passed through the pipeline to the TTS stage
