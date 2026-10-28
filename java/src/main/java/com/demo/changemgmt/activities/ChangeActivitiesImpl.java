package com.demo.changemgmt.activities;

import com.demo.changemgmt.TaskQueues;
import com.demo.changemgmt.cmdb.ServiceRegistry;
import com.demo.changemgmt.model.ChangeRequest;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityExecutionContext;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/** A Spring bean, so it gets the ServiceRegistry injected like any other component. */
@Component
@ActivityImpl(taskQueues = TaskQueues.CHANGE_MANAGEMENT)
public class ChangeActivitiesImpl implements ChangeActivities {

    private static final Logger log = LoggerFactory.getLogger(ChangeActivitiesImpl.class);
    private static final long STEP_MILLIS = 900;

    private final ServiceRegistry registry;

    public ChangeActivitiesImpl(ServiceRegistry registry) {
        this.registry = registry;
    }

    @Override
    public void validateChange(ChangeRequest request) {
        ServiceRegistry.ServiceRecord record = registry.find(request.getService())
                .orElseThrow(() -> ApplicationFailure.newNonRetryableFailure(
                        "Service '" + request.getService() + "' is not registered for automated change "
                                + "management. Changes to it require a manual CAB review.",
                        "ServiceNotChangeManaged"));

        String action = request.getAction() == null ? "" : request.getAction();
        if (!record.allowedActions().contains(action)) {
            throw ApplicationFailure.newNonRetryableFailure(
                    "Action '" + action + "' is not permitted for " + record.name(), "ActionNotPermitted");
        }
        if ("rollback".equals(action)
                && (request.getTargetVersion() == null || request.getTargetVersion().isBlank())) {
            throw ApplicationFailure.newNonRetryableFailure(
                    "A rollback needs a target version", "InvalidChangeRequest");
        }
        log.info("Validated {} for {} (owner {}, window {})",
                action, record.name(), record.owner(), record.changeWindow());
    }

    @Override
    public String openChangeRecord(ChangeRequest request) {
        // Derived from the workflow id, so a retried activity returns the same record.
        String workflowId = Activity.getExecutionContext().getInfo().getWorkflowId();
        String changeId = "CHG-" + Integer.toHexString(Math.abs(workflowId.hashCode())).toUpperCase();
        log.info("Opened change record {} for incident {}", changeId, request.getIncidentId());
        return changeId;
    }

    @Override
    public List<String> executeChange(String changeId, ChangeRequest request) {
        ActivityExecutionContext ctx = Activity.getExecutionContext();
        List<String> plan = planFor(request);

        // Resume from the last heartbeat if this is a retry after a crash.
        Optional<Integer> lastDone = ctx.getHeartbeatDetails(Integer.class);
        int start = lastDone.map(i -> i + 1).orElse(0);
        List<String> done = new ArrayList<>(plan.subList(0, Math.min(start, plan.size())));

        for (int i = start; i < plan.size(); i++) {
            sleep(STEP_MILLIS);
            done.add(plan.get(i));
            ctx.heartbeat(i);
            log.info("[{}] step {}/{}: {}", changeId, i + 1, plan.size(), plan.get(i));
        }
        return done;
    }

    @Override
    public void closeChangeRecord(String changeId, String outcome) {
        log.info("Closed change record {} as {}", changeId, outcome);
    }

    private static List<String> planFor(ChangeRequest request) {
        String service = request.getService();
        return switch (request.getAction()) {
            case "rollback" -> List.of(
                    "freeze deploys for " + service,
                    "deploy " + request.getTargetVersion() + " to canary (10%)",
                    "canary healthy; promote to 100%",
                    "unfreeze deploys");
            case "scale_out" -> List.of(
                    "raise replica count for " + service + " from 6 to 10",
                    "wait for new pods ready",
                    "rebalance traffic");
            default -> List.of(
                    "drain traffic from " + service + " pod 1/3",
                    "restart pods 1-3 in a rolling sequence",
                    "readiness checks passed");
        };
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException(e);
        }
    }
}
