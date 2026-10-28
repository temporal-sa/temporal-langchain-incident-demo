package com.demo.changemgmt.workflow;

import com.demo.changemgmt.TaskQueues;
import com.demo.changemgmt.activities.ChangeActivities;
import com.demo.changemgmt.model.ChangeRequest;
import com.demo.changemgmt.model.ChangeResult;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.List;

/**
 * Validate, record, execute, and close a change. If validation throws a non-retryable
 * ApplicationFailure (for example, the service is not under automated change
 * management), this workflow fails, and the Python parent sees a ChildWorkflowError
 * whose root cause carries the Java message and type.
 */
@WorkflowImpl(taskQueues = TaskQueues.CHANGE_MANAGEMENT)
public class ChangeRequestWorkflowImpl implements ChangeRequestWorkflow {

    private static final Logger log = Workflow.getLogger(ChangeRequestWorkflowImpl.class);

    private final ChangeActivities activities = Workflow.newActivityStub(
            ChangeActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.CHANGE_MANAGEMENT)
                    .setStartToCloseTimeout(Duration.ofSeconds(60))
                    .setHeartbeatTimeout(Duration.ofSeconds(10))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setInitialInterval(Duration.ofSeconds(1))
                            .setMaximumAttempts(5)
                            .build())
                    .build());

    private String status = "RECEIVED";

    @Override
    public ChangeResult execute(ChangeRequest request) {
        log.info("Change requested: {}", request);

        status = "VALIDATING";
        activities.validateChange(request);

        status = "RECORDING";
        String changeId = activities.openChangeRecord(request);

        status = "EXECUTING";
        List<String> steps = activities.executeChange(changeId, request);

        status = "CLOSING";
        activities.closeChangeRecord(changeId, "IMPLEMENTED");

        status = "COMPLETED";
        return new ChangeResult(changeId, "COMPLETED", steps,
                "Change " + changeId + " implemented for " + request.getService());
    }

    @Override
    public String getStatus() {
        return status;
    }
}
