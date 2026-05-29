## ADDED Requirements

### Requirement: Lottie character overlay in video
The system SHALL support an optional Lottie animated character displayed in the bottom-right corner of the video when the user enables "Include animated character".

#### Scenario: Character enabled in task config
- **WHEN** user creates a task with "Include animated character" checked
- **THEN** the rendered video SHALL display a Lottie animation (220x220px) in the bottom-right corner throughout the podcast

#### Scenario: Character disabled in task config
- **WHEN** user creates a task without checking "Include animated character"
- **THEN** the rendered video SHALL NOT include any character overlay

### Requirement: Deterministic Lottie rendering in HyperFrame
The Lottie animation SHALL be registered on `window.__hfLottie` for seek-driven deterministic rendering, following HyperFrame's Lottie adapter pattern.

#### Scenario: Lottie animation frame accuracy
- **WHEN** HyperFrame renders the composition frame-by-frame
- **THEN** the Lottie animation SHALL advance deterministically based on seek position, not wall-clock time

### Requirement: Default Lottie asset provided
The project SHALL include at least one default Lottie animation file at `assets/lottie/podcast_host.json` suitable for a podcast host character.

#### Scenario: Default asset exists
- **WHEN** the system checks for the character animation file
- **THEN** `assets/lottie/podcast_host.json` SHALL exist and be a valid Lottie JSON file
