package com.edpo.contentledger.model;

/**
 * Coarse lifecycle status for the content item as observed by the ledger.
 * NEW             – no terminal decisions yet
 * VERIFIED        – last verification = verified
 * REJECTED        – last verification = any rejection
 * REPORTED_OPEN   – report accepted/valid but no deletion yet
 * REPORT_DISMISSED – report was dismissed
 * DELETED         – post-deleted event observed
 * RESTORED        – objection-approved after deletion
 */
public enum LifecycleStatus {
    NEW,
    VERIFIED,
    REJECTED,
    REPORTED_OPEN,
    REPORT_DISMISSED,
    DELETED,
    RESTORED
}
