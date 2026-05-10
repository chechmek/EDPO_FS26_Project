The following is the feedback on the use of Sagas and Stateful Resilience Patterns discussed in class last week:

    Good use of the stateful retry pattern upon timeout or exception in sending verification request to peers (VerifyContent.bpmn).

    In ReportContent.bpmn, the “Review Objection” task appears to be modeled as a standard process step rather than an explicit human intervention for resilience. The human-intervention resilience pattern should instead be applied in situations where failure would otherwise occur, allowing the process to recover through human input and continue execution.

    I recommend abstracting a bit your sequence diagrams, focusing only on events and commands, as well as important inter-service HTTP requests (relevant to your domain). Also, I recommend highlighting the commands and events in your sequence diagrams with different colors. 
