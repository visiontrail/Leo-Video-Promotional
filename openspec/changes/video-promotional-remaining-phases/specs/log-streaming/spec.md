## ADDED Requirements

### Requirement: Real-time log streaming via SSE
The backend SHALL provide a Server-Sent Events endpoint at `GET /api/tasks/{id}/logs/stream` that streams pipeline log lines in real time.

#### Scenario: Client receives log events during processing
- **WHEN** a task is being processed and a client connects to the SSE endpoint
- **THEN** the client SHALL receive each pipeline log line as an SSE event with the log message as data

#### Scenario: SSE connection for completed task
- **WHEN** a client connects to the SSE endpoint for a completed or failed task
- **THEN** the endpoint SHALL send a final event with status "complete" or "failed" and close the connection

### Requirement: Log display in task detail UI
The task detail page SHALL display pipeline logs in a collapsible log panel that updates in real time during processing.

#### Scenario: Live logs shown during processing
- **WHEN** user views a task that is currently being processed
- **THEN** the log panel SHALL display log lines as they arrive via SSE, auto-scrolling to the latest entry

#### Scenario: Historical logs shown for completed task
- **WHEN** user views a task that has already completed
- **THEN** the log panel SHALL display the full log history from the log file

### Requirement: Pipeline log persistence
Each pipeline stage SHALL write log output to a `logs/pipeline.log` file in the task's output directory.

#### Scenario: Log file created during processing
- **WHEN** a task completes processing (success or failure)
- **THEN** the task's output directory SHALL contain `logs/pipeline.log` with all pipeline log entries
