package com.demo.changemgmt.activities;

import com.demo.changemgmt.model.ChangeRequest;
import io.temporal.activity.ActivityInterface;

import java.util.List;

@ActivityInterface
public interface ChangeActivities {

    /** Throws a non-retryable ApplicationFailure when policy forbids the change. */
    void validateChange(ChangeRequest request);

    String openChangeRecord(ChangeRequest request);

    /** Runs the change step by step, heartbeating progress so a retry resumes mid-way. */
    List<String> executeChange(String changeId, ChangeRequest request);

    void closeChangeRecord(String changeId, String outcome);
}
