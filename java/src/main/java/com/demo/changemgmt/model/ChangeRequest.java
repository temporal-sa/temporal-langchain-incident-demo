package com.demo.changemgmt.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

/**
 * Input sent by the Python IncidentWorkflow. The JSON field names (snake_case) are the
 * cross-language contract; see ChangeRequest in python/src/incident_agent/shared/models.py.
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
@JsonIgnoreProperties(ignoreUnknown = true)
public class ChangeRequest {
    private String incidentId;
    private String service;
    private String action;
    private String targetVersion;
    private String rationale;
    private String requestedBy;
    private String approvedBy;

    public ChangeRequest() {}

    public String getIncidentId() { return incidentId; }
    public void setIncidentId(String incidentId) { this.incidentId = incidentId; }
    public String getService() { return service; }
    public void setService(String service) { this.service = service; }
    public String getAction() { return action; }
    public void setAction(String action) { this.action = action; }
    public String getTargetVersion() { return targetVersion; }
    public void setTargetVersion(String targetVersion) { this.targetVersion = targetVersion; }
    public String getRationale() { return rationale; }
    public void setRationale(String rationale) { this.rationale = rationale; }
    public String getRequestedBy() { return requestedBy; }
    public void setRequestedBy(String requestedBy) { this.requestedBy = requestedBy; }
    public String getApprovedBy() { return approvedBy; }
    public void setApprovedBy(String approvedBy) { this.approvedBy = approvedBy; }

    @Override
    public String toString() {
        return action + " " + service + (targetVersion != null ? " -> " + targetVersion : "")
                + " (incident " + incidentId + ", approved by " + approvedBy + ")";
    }
}
