package com.demo.changemgmt;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Change-management worker.
 *
 * <p>The Temporal Spring Boot starter creates the WorkflowClient and a worker for the
 * "change-management" task queue, and registers every {@code @WorkflowImpl} and
 * {@code @ActivityImpl} it finds under this package.
 */
@SpringBootApplication
public class ChangeManagementApplication {
    public static void main(String[] args) {
        SpringApplication.run(ChangeManagementApplication.class, args);
    }
}
