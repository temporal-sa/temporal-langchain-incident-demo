package com.demo.changemgmt.cmdb;

import org.springframework.stereotype.Component;

import java.util.Collection;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * A stand-in for a CMDB / change-management system of record. Only services listed here
 * may be changed automatically. legacy-billing is deliberately absent: that is the
 * unrecoverable-failure scenario.
 */
@Component
public class ServiceRegistry {

    public record ServiceRecord(String name, String owner, Set<String> allowedActions, String changeWindow) {}

    private static final Set<String> ALL = Set.of("restart", "rollback", "scale_out");

    private final Map<String, ServiceRecord> services = Map.of(
            "checkout-api", new ServiceRecord("checkout-api", "team-checkout", ALL, "any"),
            "payments-api", new ServiceRecord("payments-api", "team-payments", ALL, "any, with approval"),
            "inventory-api", new ServiceRecord("inventory-api", "team-fulfilment", ALL, "any"),
            "search-api", new ServiceRecord("search-api", "team-discovery", ALL, "any"),
            "orders-api", new ServiceRecord("orders-api", "team-orders", ALL, "any")
    );

    public Optional<ServiceRecord> find(String service) {
        return Optional.ofNullable(services.get(service));
    }

    public Collection<ServiceRecord> all() {
        return services.values();
    }
}
