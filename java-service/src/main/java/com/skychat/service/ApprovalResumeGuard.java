package com.skychat.service;

import org.springframework.stereotype.Service;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Best-effort in-process guard that stops the controller and the resume reconciler from
 * calling the agent for the same approval at the same moment.
 *
 * <p>This is an optimisation, not a correctness requirement: the persisted
 * {@code resume_request_id} is unique per decision attempt and the Python agent deduplicates
 * on it, so a duplicated call is harmless. A multi-instance deployment would need a real
 * distributed lock instead; the guard deliberately stays in-process.</p>
 */
@Service
public class ApprovalResumeGuard {
    private final ConcurrentHashMap<String, Boolean> inFlight = new ConcurrentHashMap<>();

    /**
     * @return {@code true} when this caller acquired the approval, {@code false} when another
     *         resume for the same approval is already in flight.
     */
    public boolean tryAcquire(String approvalId) {
        if (approvalId == null || approvalId.isBlank()) {
            return false;
        }
        return inFlight.putIfAbsent(approvalId, Boolean.TRUE) == null;
    }

    public void release(String approvalId) {
        if (approvalId != null && !approvalId.isBlank()) {
            inFlight.remove(approvalId);
        }
    }
}
