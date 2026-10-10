package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Reads the agent service's SSE events into the handful of facts the Java side
 * needs to persist.
 *
 * <p>Kept as its own class rather than a private method because these are the
 * boundaries where a stream and durable state meet, and they are the parts worth
 * testing on their own:
 *
 * <ul>
 *   <li><b>Unknown event types are ignored.</b> The context engine added a
 *       {@code context_report} event to an existing protocol; a server that threw on
 *       an unknown type would break every time the client evolved, so the rule is
 *       "recognise what you know, ignore the rest". The frontend parser has the same
 *       rule, which is what makes the addition backward compatible.</li>
 *   <li><b>A malformed frame never breaks the answer.</b> The reply has already been
 *       streamed by the time this runs; losing a diagnostic must not lose the turn.</li>
 *   <li><b>The newest context report wins.</b> A resumed run re-assembles the prompt,
 *       so the last report describes the prompt that actually produced the answer.</li>
 * </ul>
 */
public class StreamEventAccumulator {
    private static final Logger log = LoggerFactory.getLogger(StreamEventAccumulator.class);

    private final ObjectMapper objectMapper;
    private final AtomicReference<String> assistantText = new AtomicReference<>("");
    private final AtomicReference<String> title = new AtomicReference<>("");
    private final AtomicBoolean failed = new AtomicBoolean(false);
    private final AtomicReference<JsonNode> contextReport = new AtomicReference<>();

    public StreamEventAccumulator(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /**
     * Read one server frame.
     *
     * @param data the raw SSE `data:` payload
     */
    public void accept(String data) {
        if (data == null || data.isBlank()) {
            return;
        }
        try {
            JsonNode event = objectMapper.readTree(data);
            String type = event.path("type").asText("");
            switch (type) {
                case "answer" -> {
                    if (event.has("content")) {
                        assistantText.set(assistantText.get() + event.get("content").asText());
                    }
                }
                case "conversation_title" -> {
                    if (event.has("content")) {
                        title.set(event.get("content").asText());
                    }
                }
                case "context_report" -> {
                    if (event.has("report") && !event.get("report").isNull()) {
                        contextReport.set(event.get("report"));
                    }
                }
                case "error" -> failed.set(true);
                default -> {
                    // Deliberately ignored: see the class comment.
                }
            }
        } catch (Exception error) {
            // A frame that is not JSON, or not the shape we expect, is not a reason
            // to fail a turn whose answer is already on the wire.
            log.debug("Ignoring an unreadable SSE frame", error);
        }
    }

    public String assistantText() {
        return assistantText.get();
    }

    public String title() {
        return title.get();
    }

    public boolean failed() {
        return failed.get();
    }

    public JsonNode contextReport() {
        return contextReport.get();
    }
}
