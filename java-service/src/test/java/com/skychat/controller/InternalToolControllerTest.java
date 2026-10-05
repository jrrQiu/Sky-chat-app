package com.skychat.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.config.AgentInternalProperties;
import com.skychat.config.ClientAddressResolver;
import com.skychat.domain.ticket.Ticket;
import com.skychat.dto.TicketActivityView;
import com.skychat.dto.TicketCommentView;
import com.skychat.dto.TicketDetail;
import com.skychat.dto.TicketSummary;
import com.skychat.service.AuditService;
import com.skychat.service.AuthException;
import com.skychat.service.InternalTokenService;
import com.skychat.service.JwtSigner;
import com.skychat.service.TicketService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The internal tool endpoint: credential handling, delegation to {@link TicketService}, and the
 * failure shape the agent sees.
 */
class InternalToolControllerTest {

    private static final String SECRET = "internal-jwt-secret-for-tests";

    private final JwtSigner signer = new JwtSigner(new ObjectMapper());
    private InternalTokenService tokens;
    private FakeTicketService tickets;
    private FakeAuditService audits;
    private InternalToolController controller;

    @BeforeEach
    void setUp() {
        tokens = new InternalTokenService(new AgentInternalProperties(SECRET, null), signer);
        tickets = new FakeTicketService();
        audits = new FakeAuditService();
        controller = new InternalToolController(
                tokens, tickets, audits, new ClientAddressResolver(null)
        );
    }

    private String callerToken(String userId, List<String> roles) {
        return signer.sign(
                SECRET, InternalTokenService.CALLER_ISSUER,
                InternalTokenService.CALLER_AUDIENCE, userId, roles, 60
        );
    }

    private ResponseEntity<Object> invoke(String token, String tool, Map<String, Object> body) {
        MockServerHttpRequest.BaseBuilder<?> request = MockServerHttpRequest.post("/v1/internal/tools/" + tool);
        if (token != null) {
            request = request.header("Authorization", "Bearer " + token);
        }
        return controller
                .invoke(
                        token == null ? null : "Bearer " + token,
                        tool,
                        body,
                        MockServerWebExchange.from(request)
                )
                .block();
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> bodyOf(ResponseEntity<Object> response) {
        return (Map<String, Object>) response.getBody();
    }

    // ------------------------------------------------------------------ credential

    @Test
    @DisplayName("a missing or invalid credential is 401, not a tool failure")
    void requiresAValidCredential() {
        ResponseEntity<Object> missing = invoke(null, "ticket_list", Map.of());
        assertThat(missing.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        assertThat(bodyOf(missing)).containsEntry("error", "INVALID_INTERNAL_CALLER");

        ResponseEntity<Object> garbage = invoke("not-a-token", "ticket_list", Map.of());
        assertThat(garbage.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    /** The confused-deputy case: a valid signature addressed to the other service. */
    @Test
    @DisplayName("a token Java minted for the agent service cannot be replayed here")
    void rejectsReplayedOutboundToken() {
        String outbound = tokens.createToken("victim", List.of("admin"));

        ResponseEntity<Object> response = invoke(outbound, "ticket_list", Map.of());

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        assertThat(tickets.lastCaller).isNull();
    }

    // ------------------------------------------------------------------ delegation

    @Test
    @DisplayName("ticket_create acts as the signed subject and never as a header")
    void createActsAsTheSignedSubject() {
        tickets.detail = FakeTicketService.detail("t1", 1042, "incident", "new", "high", "打印机离线");

        ResponseEntity<Object> response = invoke(
                callerToken("user-7", List.of("employee")),
                "ticket_create",
                Map.of("title", "打印机离线", "type", "incident", "impact", "high", "urgency", "high")
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(bodyOf(response)).containsEntry("success", true);
        assertThat(String.valueOf(bodyOf(response).get("text"))).contains("#1042");
        assertThat(tickets.lastCaller.userId()).isEqualTo("user-7");
        assertThat(tickets.lastCaller.roles()).containsExactly("employee");
        // source=chat, so a ticket raised from a conversation is traceable back to it.
        assertThat(tickets.lastSource).isEqualTo("chat");
        // The agent is never allowed to choose the priority; the matrix does.
        assertThat(tickets.lastPriorityOverride).isNull();
        assertThat(audits.actions).contains("ticket.create");
    }

    @Test
    @DisplayName("a business refusal is a normal tool outcome, not an HTTP error")
    void businessRefusalIsAToolOutcome() {
        tickets.failure = new AuthException(TicketService.FORBIDDEN_TICKET_ACTION, "需要处理人权限");

        ResponseEntity<Object> response = invoke(
                callerToken("user-7", List.of("employee")),
                "ticket_create",
                Map.of("title", "越权尝试")
        );

        // The agent should be able to say "you are not allowed" instead of reporting a crash.
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(bodyOf(response)).containsEntry("success", false);
        assertThat(bodyOf(response)).containsEntry("error", TicketService.FORBIDDEN_TICKET_ACTION);
    }

    @Test
    @DisplayName("an unknown tool is refused without touching the ticket service")
    void unknownTool() {
        ResponseEntity<Object> response = invoke(
                callerToken("user-7", List.of("employee")), "email_send", Map.of()
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(bodyOf(response)).containsEntry("success", false);
        assertThat(String.valueOf(bodyOf(response).get("text"))).contains("未知工具");
        assertThat(tickets.lastCaller).isNull();
    }

    @Test
    @DisplayName("the generic adapter envelope is accepted as well as a bare argument object")
    void acceptsBothEnvelopeShapes() {
        tickets.detail = FakeTicketService.detail("t1", 7, "request", "new", "low", "申请显示器");

        invoke(callerToken("user-7", List.of("employee")), "ticket_create",
                Map.of("args", Map.of("title", "申请显示器"), "state", Map.of("user_id", "spoofed")));

        // The nested args were used and the state was ignored: state is not an identity channel.
        assertThat(tickets.lastTitle).isEqualTo("申请显示器");
        assertThat(tickets.lastCaller.userId()).isEqualTo("user-7");
    }

    @Test
    @DisplayName("ticket_list asks for the caller's own tickets")
    void listScopesToTheCaller() {
        tickets.page = new TicketService.Page(
                List.of(TicketSummary.of(FakeTicketService.ticket("t1", 11, "new"))),
                1, 0, 5, Map.of(), Map.of()
        );

        ResponseEntity<Object> response = invoke(
                callerToken("user-7", List.of("employee")), "ticket_list", Map.of("limit", 5)
        );

        assertThat(bodyOf(response)).containsEntry("success", true);
        assertThat(String.valueOf(bodyOf(response).get("text"))).contains("#11");
        // Resolved to the caller's id, not the literal "me": the service filters on this value
        // in SQL, so a literal would silently return nothing.
        assertThat(tickets.lastRequester).isEqualTo("user-7");
    }

    @Test
    @DisplayName("ticket_get accepts a number and resolves it through the visible list")
    void getResolvesByNumber() {
        tickets.page = new TicketService.Page(
                List.of(TicketSummary.of(FakeTicketService.ticket("t9", 9, "new"))),
                1, 0, 1, Map.of(), Map.of()
        );
        tickets.detail = FakeTicketService.detail("t9", 9, "incident", "in_progress", "medium", "网络抖动");

        ResponseEntity<Object> response = invoke(
                callerToken("user-7", List.of("employee")), "ticket_get", Map.of("number", 9)
        );

        assertThat(bodyOf(response)).containsEntry("success", true);
        assertThat(String.valueOf(bodyOf(response).get("text"))).contains("#9");
        // Resolution went through the caller-scoped list, so an invisible number stays invisible.
        assertThat(tickets.lastQuery).isEqualTo("9");
    }

    @Test
    @DisplayName("an invisible ticket number yields a refusal rather than a confirmation")
    void invisibleNumberIsNotFound() {
        tickets.page = new TicketService.Page(List.of(), 0, 0, 1, Map.of(), Map.of());

        ResponseEntity<Object> response = invoke(
                callerToken("user-7", List.of("employee")), "ticket_get", Map.of("number", 4242)
        );

        assertThat(bodyOf(response)).containsEntry("success", false);
        assertThat(String.valueOf(bodyOf(response).get("text"))).contains("ticketId");
    }

    @Test
    @DisplayName("ticket_comment forwards the visibility and is audited")
    void commentForwardsVisibility() {
        tickets.detail = FakeTicketService.detail("t1", 5, "incident", "open", "low", "标题");

        invoke(callerToken("agent-1", List.of("it_staff")), "ticket_comment",
                Map.of("ticketId", "t1", "body", "内部说明", "visibility", "internal"));

        assertThat(tickets.lastVisibility).isEqualTo("internal");
        assertThat(audits.actions).contains("ticket.comment");
        assertThat(audits.origins).contains("agent");
    }

    @Test
    @DisplayName("ticket_merge merges the two named tickets and audits the merge")
    void mergeResolvesAndDelegates() {
        tickets.page = new TicketService.Page(
                List.of(TicketSummary.of(FakeTicketService.ticket("t9", 9, "new"))),
                1, 0, 1, Map.of(), Map.of()
        );
        tickets.mergeResult = new TicketService.MergeResult(
                FakeTicketService.detail("t9", 9, "incident", "closed", "medium", "重复单"),
                FakeTicketService.detail("t12", 12, "incident", "open", "medium", "主单")
        );

        ResponseEntity<Object> response = invoke(
                callerToken("staff-1", List.of("it_staff")),
                "ticket_merge",
                Map.of("sourceNumber", 9, "targetTicketId", "t12")
        );

        assertThat(bodyOf(response)).containsEntry("success", true);
        assertThat(String.valueOf(bodyOf(response).get("text"))).contains("#9").contains("#12");
        assertThat(tickets.mergeSource).isEqualTo("t9");
        assertThat(tickets.mergeTarget).isEqualTo("t12");
        assertThat(tickets.lastCaller.userId()).isEqualTo("staff-1");
        assertThat(audits.actions).contains("ticket.merge");
    }

    @Test
    @DisplayName("a merge without two tickets is refused and never reaches the service")
    void mergeNeedsTwoTickets() {
        ResponseEntity<Object> response = invoke(
                callerToken("staff-1", List.of("it_staff")),
                "ticket_merge",
                Map.of("sourceTicketId", "t9")
        );

        assertThat(bodyOf(response)).containsEntry("success", false);
        assertThat(String.valueOf(bodyOf(response).get("text"))).contains("未执行合并");
        assertThat(tickets.mergeSource).isNull();
    }

    @Test
    @DisplayName("a refused merge keeps its business error code instead of becoming a 500")
    void mergeRefusalIsAToolOutcome() {
        tickets.failure = new AuthException(TicketService.MERGE_SELF, "不能把工单合并到它自己");

        ResponseEntity<Object> response = invoke(
                callerToken("staff-1", List.of("it_staff")),
                "ticket_merge",
                Map.of("sourceTicketId", "t9", "targetTicketId", "t9")
        );

        // The refusal is a normal tool outcome the agent can relay, not an HTTP error.
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(bodyOf(response)).containsEntry("success", false);
        assertThat(bodyOf(response)).containsEntry("error", TicketService.MERGE_SELF);
    }

    // ------------------------------------------------------------------ fakes

    private static final class FakeTicketService extends TicketService {
        private TicketDetail detail;
        private TicketService.Page page = new TicketService.Page(
                List.of(), 0, 0, 5, Map.of(), Map.of()
        );
        private AuthException failure;
        private TicketService.Caller lastCaller;
        private String lastSource;
        private String lastPriorityOverride;
        private String lastTitle;
        private String lastRequester;
        private String lastQuery;
        private String lastVisibility;
        private TicketService.MergeResult mergeResult;
        private String mergeSource;
        private String mergeTarget;

        FakeTicketService() {
            super(null, null, null, null, null);
        }

        static Ticket ticket(String id, long number, String status) {
            Ticket ticket = new Ticket();
            ticket.setId(id);
            ticket.setNumber(number);
            ticket.setType("incident");
            ticket.setStatus(status);
            ticket.setPriority("medium");
            ticket.setTitle("工单 " + number);
            ticket.setRequesterId("user-7");
            ticket.setCreatedAt(LocalDateTime.now());
            ticket.setUpdatedAt(LocalDateTime.now());
            return ticket;
        }

        static TicketDetail detail(
                String id, long number, String type, String status, String priority, String title
        ) {
            return TicketDetail.of(
                    ticket(id, number, status),
                    List.of(new TicketCommentView(
                            "c1", id, "agent-1", "it_staff", "public", "我们正在处理", LocalDateTime.now()
                    )),
                    List.<TicketActivityView>of(),
                    List.of(),
                    List.of()
            );
        }

        @Override
        public TicketDetail create(
                Caller caller, String type, String title, String description, String impact,
                String urgency, String requestedPriority, String assigneeId, String parentId,
                String requesterId, String source, String conversationId
        ) {
            if (failure != null) {
                throw failure;
            }
            lastCaller = caller;
            lastSource = source;
            lastPriorityOverride = requestedPriority;
            lastTitle = title;
            return detail;
        }

        @Override
        public TicketDetail get(Caller caller, String id) {
            if (failure != null) {
                throw failure;
            }
            lastCaller = caller;
            return detail;
        }

        @Override
        public Page list(
                Caller caller, String query, String status, String type, String priority,
                String assigneeId, String requesterId, boolean includeMerged, int page, int size
        ) {
            if (failure != null) {
                throw failure;
            }
            lastCaller = caller;
            lastRequester = requesterId;
            lastQuery = query;
            return this.page;
        }

        @Override
        public TicketDetail addComment(
                Caller caller, String id, String body, String visibility
        ) {
            if (failure != null) {
                throw failure;
            }
            lastCaller = caller;
            lastVisibility = visibility;
            return detail;
        }

        @Override
        public MergeResult merge(Caller caller, String sourceId, String targetId) {
            if (failure != null) {
                throw failure;
            }
            lastCaller = caller;
            mergeSource = sourceId;
            mergeTarget = targetId;
            return mergeResult;
        }
    }

    private static final class FakeAuditService extends AuditService {
        private final List<String> actions = new ArrayList<>();
        private final List<String> origins = new ArrayList<>();

        FakeAuditService() {
            super(null, new ObjectMapper());
        }

        @Override
        public void record(
                Context context,
                String action,
                String objectType,
                String objectId,
                String outcome,
                Map<String, Object> detail
        ) {
            actions.add(action);
            if (detail != null && detail.get("origin") != null) {
                origins.add(String.valueOf(detail.get("origin")));
            }
        }
    }
}
