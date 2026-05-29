## ADDED Requirements

### Requirement: Multiple AI provider storage
The system SHALL support storing multiple AI provider configurations (endpoint, API key, model name) in the SQLite database.

#### Scenario: Add a new AI provider
- **WHEN** user adds a provider with endpoint "http://localhost:11434/v1/chat/completions", model "llama3", and API key
- **THEN** the provider SHALL be persisted in the database and available for selection

### Requirement: AI provider selection in task creation
The task creation form SHALL include a dropdown to select which AI provider to use for content digestion.

#### Scenario: User selects a specific provider
- **WHEN** user creates a task and selects "Local Ollama" from the AI provider dropdown
- **THEN** the pipeline SHALL use that provider's endpoint and model for summarization and script generation

#### Scenario: Default provider used when none selected
- **WHEN** user creates a task without selecting a provider
- **THEN** the system SHALL use the provider marked as default

### Requirement: AI provider management UI
The settings page SHALL allow users to add, edit, delete, and set default AI providers.

#### Scenario: User manages providers in settings
- **WHEN** user navigates to the settings page
- **THEN** the page SHALL display all configured providers with options to edit, delete, or set as default

### Requirement: Provider CRUD API endpoints
The backend SHALL provide REST endpoints for AI provider management.

#### Scenario: List providers
- **WHEN** a GET request is sent to `/api/providers`
- **THEN** the system SHALL return all configured providers (with API keys masked)

#### Scenario: Create provider
- **WHEN** a POST request is sent to `/api/providers` with valid provider data
- **THEN** the system SHALL create the provider and return it with a generated ID
