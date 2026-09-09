from enum import StrEnum


class DeliveryMode(StrEnum):
    MOCK = "mock"
    TEST = "test"
    LIVE = "live"


class CampaignType(StrEnum):
    COLD = "cold"
    ONBOARDING = "onboarding"
    PROMOTIONAL = "promotional"
    REENGAGEMENT = "reengagement"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class AudienceSegment(StrEnum):
    PROSPECT = "prospect"
    OPTED_IN_USER = "opted_in_user"
    CUSTOMER = "customer"
    INACTIVE_SUBSCRIBER = "inactive_subscriber"


class LifecycleStatus(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    CONVERTED = "converted"
    INACTIVE = "inactive"
    SUPPRESSED = "suppressed"


class ConsentStatus(StrEnum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


class ConsentTopic(StrEnum):
    MARKETING = "marketing"
    TRANSACTIONAL = "transactional"
    COLD_OUTREACH = "cold_outreach"


class EnrollmentStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"
    PENDING_CANCELLATION = "pending_cancellation"


class MessageStatus(StrEnum):
    DRAFTED = "drafted"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    SUBMITTING = "submitting"
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELED = "canceled"
    SIMULATED = "simulated"


class JobStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELED = "canceled"


class JobType(StrEnum):
    DRAFT_AND_SEND = "draft_and_send"
    COLD_ENROLL = "cold_enroll"
    PROVIDER_STOP = "provider_stop"
    PROCESS_EVENT = "process_event"
    CLASSIFY_REPLY = "classify_reply"


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    ARCHIVED = "archived"


class OfferStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXPIRED = "expired"
    REDEEMED = "redeemed"


class ProviderName(StrEnum):
    MOCK_MAIL = "mock_mail"
    SENDGRID = "sendgrid"
    MOCK_SEQUENCER = "mock_sequencer"
    SMARTLEAD = "smartlead"
    N8N_INBOX = "n8n_inbox"
    DEMO = "demo"
    HUBSPOT = "hubspot"


class ReplyIntent(StrEnum):
    INTERESTED = "interested"
    QUESTION = "question"
    NOT_NOW = "not_now"
    NEGATIVE = "negative"
    UNSUBSCRIBE = "unsubscribe"
    OUT_OF_OFFICE = "out_of_office"
    WRONG_PERSON = "wrong_person"
    UNKNOWN = "unknown"


class SalesTaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DISMISSED = "dismissed"


class SuppressionScope(StrEnum):
    GLOBAL = "global"
    MARKETING = "marketing"
    COLD = "cold"
    TOPIC = "topic"


class IntegrationHealth(StrEnum):
    DISCONNECTED = "disconnected"
    MOCK = "mock"
    CONFIGURED = "configured"
    ERROR = "error"
