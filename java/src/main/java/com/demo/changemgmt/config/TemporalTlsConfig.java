package com.demo.changemgmt.config;

import io.grpc.netty.shaded.io.grpc.netty.GrpcSslContexts;
import io.grpc.netty.shaded.io.netty.handler.ssl.SslContextBuilder;
import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import io.temporal.spring.boot.TemporalOptionsCustomizer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import javax.net.ssl.SSLException;
import java.io.File;

/**
 * TLS for a self-hosted Temporal service whose certificate is signed by a private CA.
 *
 * <p>Only active when TEMPORAL_TLS_CA is set, so it has no effect on the default
 * plaintext setup or on the API-key and public-CA mTLS profiles. If TEMPORAL_TLS_CERT
 * and TEMPORAL_TLS_KEY are also set, they're presented as the client certificate
 * (mTLS). The key must be PEM-encoded PKCS#8.
 */
@Configuration
public class TemporalTlsConfig {

    private static final Logger log = LoggerFactory.getLogger(TemporalTlsConfig.class);

    @Bean
    @ConditionalOnExpression("!'${TEMPORAL_TLS_CA:}'.isEmpty()")
    public TemporalOptionsCustomizer<WorkflowServiceStubsOptions.Builder> privateCaTls(
            @Value("${TEMPORAL_TLS_CA}") String caPath,
            @Value("${TEMPORAL_TLS_CERT:}") String certPath,
            @Value("${TEMPORAL_TLS_KEY:}") String keyPath) {
        return builder -> {
            try {
                SslContextBuilder ssl = GrpcSslContexts.forClient().trustManager(new File(caPath));
                boolean mtls = !certPath.isBlank() && !keyPath.isBlank();
                if (mtls) {
                    ssl.keyManager(new File(certPath), new File(keyPath));
                }
                log.info("Temporal TLS: trusting CA {}{}", caPath, mtls ? ", client certificate " + certPath : "");
                builder.setSslContext(ssl.build());
                return builder;
            } catch (SSLException e) {
                throw new IllegalStateException("Could not build the TLS context for Temporal from TEMPORAL_TLS_CA", e);
            }
        };
    }
}
