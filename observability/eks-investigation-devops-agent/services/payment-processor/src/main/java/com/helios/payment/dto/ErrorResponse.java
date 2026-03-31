package com.helios.payment.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.*;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * Structured error response DTO.
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ErrorResponse {

    private ErrorDetail error;

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public static class ErrorDetail {
        private String code;
        private String message;
        private String correlationId;
        private OffsetDateTime timestamp;
        private List<FieldError> details;
    }

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    public static class FieldError {
        private String field;
        private String issue;
    }

    public static ErrorResponse of(String code, String message, String correlationId) {
        return ErrorResponse.builder()
                .error(ErrorDetail.builder()
                        .code(code)
                        .message(message)
                        .correlationId(correlationId)
                        .timestamp(OffsetDateTime.now())
                        .build())
                .build();
    }

    public static ErrorResponse of(String code, String message, String correlationId, List<FieldError> details) {
        return ErrorResponse.builder()
                .error(ErrorDetail.builder()
                        .code(code)
                        .message(message)
                        .correlationId(correlationId)
                        .timestamp(OffsetDateTime.now())
                        .details(details)
                        .build())
                .build();
    }
}
