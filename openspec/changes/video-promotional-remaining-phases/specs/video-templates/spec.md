## ADDED Requirements

### Requirement: Multiple video template styles
The system SHALL provide at least 3 distinct video template styles selectable from the task creation form.

#### Scenario: User selects a video template
- **WHEN** user creates a new task and selects "Kinetic Text" template
- **THEN** the composed video SHALL use the kinetic text visual style with animated text reveals

#### Scenario: Default template applied when none selected
- **WHEN** user creates a task without selecting a template
- **THEN** the system SHALL use the default "Podcast Studio" dark theme template

### Requirement: Title card at video start
Each video SHALL begin with a 5-second title card displaying the content title and "A Podcast Discussion" subtitle with fade-in animation.

#### Scenario: Title card renders correctly
- **WHEN** a video is rendered from a source titled "The Art of Learning"
- **THEN** the first 5 seconds of the video SHALL display "The Art of Learning" as a centered title with fade-in animation

### Requirement: Outro card at video end
Each video SHALL end with a 5-second outro card with fade-out animation.

#### Scenario: Outro card appended after content
- **WHEN** the podcast audio finishes
- **THEN** the video SHALL display an outro card for 5 seconds before the video ends

### Requirement: Scene transitions between segments
The video SHALL use smooth transitions (fade, slide, or cross-dissolve) between speaker segments rather than hard cuts.

#### Scenario: Transition between speaker turns
- **WHEN** the text overlay changes from Speaker 1 to Speaker 2
- **THEN** the transition SHALL use a smooth fade effect lasting 0.3-0.5 seconds
