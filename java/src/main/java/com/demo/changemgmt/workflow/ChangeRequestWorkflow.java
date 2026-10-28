package com.demo.changemgmt.workflow;

import com.demo.changemgmt.model.ChangeRequest;
import com.demo.changemgmt.model.ChangeResult;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/**
 * Workflow type "ChangeRequestWorkflow" (the interface's simple name). The Python agent
 * starts it by that name on the "change-management" task queue.
 */
@WorkflowInterface
public interface ChangeRequestWorkflow {

    @WorkflowMethod
    ChangeResult execute(ChangeRequest request);

    @QueryMethod
    String getStatus();
}
