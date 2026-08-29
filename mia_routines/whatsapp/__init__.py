"""WhatsApp notification triage.

The phone forwards WhatsApp *notifications* (never the WhatsApp protocol) to
`server.py`. Rules decide urgency deterministically; only what the rules cannot
settle is sent to the model. Nothing in this package can write to WhatsApp —
there is no WhatsApp client here, by design.
"""
