package com.demo.changemgmt.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import java.util.ArrayList;
import java.util.List;

/** Result returned to the Python IncidentWorkflow (snake_case JSON). */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
@JsonIgnoreProperties(ignoreUnknown = true)
public class ChangeResult {
    private String changeId;
    private String status;
    private List<String> steps = new ArrayList<>();
    private String message;

    public ChangeResult() {}

    public ChangeResult(String changeId, String status, List<String> steps, String message) {
        this.changeId = changeId;
        this.status = status;
        this.steps = steps;
        this.message = message;
    }

    public String getChangeId() { return changeId; }
    public void setChangeId(String changeId) { this.changeId = changeId; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public List<String> getSteps() { return steps; }
    public void setSteps(List<String> steps) { this.steps = steps; }
    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }
}
