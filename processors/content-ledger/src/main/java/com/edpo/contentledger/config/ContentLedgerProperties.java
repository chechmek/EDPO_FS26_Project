package com.edpo.contentledger.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "contentledger")
public class ContentLedgerProperties {
    private Topics topics = new Topics();
    private Store store = new Store();

    public Topics getTopics() { return topics; }
    public void setTopics(Topics topics) { this.topics = topics; }
    public Store getStore() { return store; }
    public void setStore(Store store) { this.store = store; }

    public static class Topics {
        private String verificationNotification;
        private String reportNotification;
        private String postDeleted;
        private String objectionApproved;
        private String output;

        public String getVerificationNotification() { return verificationNotification; }
        public void setVerificationNotification(String v) { this.verificationNotification = v; }
        public String getReportNotification() { return reportNotification; }
        public void setReportNotification(String v) { this.reportNotification = v; }
        public String getPostDeleted() { return postDeleted; }
        public void setPostDeleted(String v) { this.postDeleted = v; }
        public String getObjectionApproved() { return objectionApproved; }
        public void setObjectionApproved(String v) { this.objectionApproved = v; }
        public String getOutput() { return output; }
        public void setOutput(String v) { this.output = v; }
    }

    public static class Store {
        private String contentState;

        public String getContentState() { return contentState; }
        public void setContentState(String v) { this.contentState = v; }
    }
}
