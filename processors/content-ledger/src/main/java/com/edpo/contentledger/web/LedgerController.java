package com.edpo.contentledger.web;

import java.util.List;
import java.util.Map;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.edpo.contentledger.model.ContentDecisionState;
import com.edpo.contentledger.model.Decision;

@RestController
@RequestMapping("/api")
public class LedgerController {

    private final LedgerQueryService queryService;

    public LedgerController(LedgerQueryService queryService) {
        this.queryService = queryService;
    }

    @GetMapping("/content")
    public Map<String, Object> listContent(
            @RequestParam(name = "limit", defaultValue = "200") int limit,
            @RequestParam(name = "withState", defaultValue = "true") boolean withState) {
        if (withState) {
            List<ContentDecisionState> states = queryService.listStates(limit);
            return Map.of(
                    "count", states.size(),
                    "items", states.stream().map(LedgerController::summary).toList()
            );
        }
        List<String> ids = queryService.listContentIds(limit);
        return Map.of("count", ids.size(), "items", ids);
    }

    @GetMapping("/content/{contentId}/state")
    public ResponseEntity<Object> getState(@PathVariable String contentId) {
        return queryService.findById(contentId)
                .<ResponseEntity<Object>>map(s -> ResponseEntity.ok(summary(s)))
                .orElseGet(() -> ResponseEntity.status(404)
                        .body(Map.of("error", "not_found", "contentId", contentId)));
    }

    @GetMapping("/content/{contentId}/decision-trace")
    public ResponseEntity<Object> getTrace(@PathVariable String contentId) {
        return queryService.findById(contentId)
                .<ResponseEntity<Object>>map(s -> ResponseEntity.ok(Map.of(
                        "contentId", s.getContentId(),
                        "decisionCount", s.getDecisionCount(),
                        "decisions", s.getDecisionTrace()
                )))
                .orElseGet(() -> ResponseEntity.status(404)
                        .body(Map.of("error", "not_found", "contentId", contentId)));
    }

    @GetMapping("/health/stream")
    public LedgerQueryService.StreamHealth streamHealth() {
        return queryService.streamHealth();
    }

    private static Map<String, Object> summary(ContentDecisionState s) {
        Decision last = s.getDecisionTrace().isEmpty()
                ? null
                : s.getDecisionTrace().get(s.getDecisionTrace().size() - 1);
        return Map.of(
                "contentId", s.getContentId(),
                "lifecycleStatus", s.getLifecycleStatus(),
                "lastVerificationStatus", nullable(s.getLastVerificationStatus()),
                "lastReportStatus", nullable(s.getLastReportStatus()),
                "deleted", s.isDeleted(),
                "restored", s.isRestored(),
                "decisionCount", s.getDecisionCount(),
                "firstSeenAt", nullable(s.getFirstSeenAt()),
                "lastUpdatedAt", nullable(s.getLastUpdatedAt()),
                "lastDecision", last == null ? "" : last
        );
    }

    private static Object nullable(Object v) {
        return v == null ? "" : v;
    }
}
