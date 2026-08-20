# Durable run state and step isolation

The Agent stores authoritative run and step status in SQLite and keeps hashed artifacts in an isolated run directory, rather than treating conversation history or generated files as workflow state. A render failure therefore changes only the affected installation step; recovery re-enters through `AgentCore`, and a clarification may invalidate only that step and its dependency descendants while unrelated outputs remain unchanged.
