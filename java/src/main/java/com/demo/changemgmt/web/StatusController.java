package com.demo.changemgmt.web;

import com.demo.changemgmt.cmdb.ServiceRegistry;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Collection;
import java.util.Map;

/** Small HTTP surface for inspecting the stand-in CMDB: curl localhost:8081/services */
@RestController
public class StatusController {

    private final ServiceRegistry registry;

    public StatusController(ServiceRegistry registry) {
        this.registry = registry;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of("ok", true, "taskQueue", "change-management");
    }

    @GetMapping("/services")
    public Collection<ServiceRegistry.ServiceRecord> services() {
        return registry.all();
    }
}
