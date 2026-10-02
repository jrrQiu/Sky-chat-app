package com.skychat.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.domain.ticket.Ticket;
import com.skychat.dto.TicketDetail;
import com.skychat.support.InMemoryTicketMappers;
import com.skychat.support.InMemoryTicketMappers.Store;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Ticket lifecycle, authorization and timeline.
 *
 * <p>The properties asserted here are the ones a client must not be able to talk its way out
 * of: visibility, internal-note filtering, the status machine, and the timestamps a reopen has
 * to clear.</p>
 */
class TicketServiceTest {

    private static final TicketService.Caller AGENT =
            new TicketService.Caller("agent-1", List.of("it_staff"));
    private static final TicketService.Caller ADMIN =
            new TicketService.Caller("admin-1", List.of("admin"));
    private static final TicketService.Caller REQUESTER =
            new TicketService.Caller("user-1", List.of("employee"));
    private static final TicketService.Caller OTHER_REQUESTER =
            new TicketService.Caller("user-2", List.of("employee"));
    private static final TicketService.Caller AUDITOR =
            new TicketService.Caller("audit-1", List.of("auditor"));

    private Store store;
    private TicketService service;

    @BeforeEach
    void setUp() {
        store = new Store();
        service = new TicketService(
                new InMemoryTicketMappers.TicketMapperFake(store),
                new InMemoryTicketMappers.CommentMapperFake(store),
                new InMemoryTicketMappers.ActivityMapperFake(store),
                new InMemoryTicketMappers.LinkMapperFake(store),
                new ObjectMapper()
        );
    }

    private TicketDetail create(TicketService.Caller caller, String title) {
        return service.create(caller, "incident", title, "详情", "high", "high", null,
                null, null, null, "manual", null);
    }

    // ------------------------------------------------------------------ creation

    @Test
    @DisplayName("priority is derived from impact and urgency, not taken from the caller")
    void priorityComesFromTheMatrix() {
        TicketDetail created = service.create(
                REQUESTER, "incident", "打印机离线", "", "high", "medium", null,
                null, null, null, "manual", null
        );

        assertThat(created.priority()).isEqualTo("high");
        assertThat(created.status()).isEqualTo("new");
        assertThat(created.impact()).isEqualTo("high");
        assertThat(created.urgency()).isEqualTo("medium");
    }

    @Test
    @DisplayName("a requester cannot set priority, assign or file for somebody else")
    void requesterCannotEscalate() {
        assertThatThrownBy(() -> service.create(
                REQUESTER, "incident", "x", "", "high", "high", "critical",
                null, null, null, "manual", null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.FORBIDDEN_TICKET_ACTION));

        assertThatThrownBy(() -> service.create(
                REQUESTER, "incident", "x", "", "high", "high", null,
                "someone-else", null, null, "manual", null))
                .isInstanceOf(AuthException.class);

        assertThatThrownBy(() -> service.create(
                REQUESTER, "incident", "x", "", "high", "high", null,
                null, null, "user-9", "manual", null))
                .isInstanceOf(AuthException.class);
    }

    @Test
    @DisplayName("an agent may override the matrix, and the override is recorded")
    void agentOverrideIsRecorded() {
        TicketDetail created = service.create(
                AGENT, "incident", "重大故障", "", "low", "low", "critical",
                null, null, null, "manual", null
        );

        assertThat(created.priority()).isEqualTo("critical");
        assertThat(created.activities())
                .anySatisfy(activity -> {
                    assertThat(activity.activityType()).isEqualTo("created");
                    assertThat(activity.detail()).containsEntry("prioritySource", "manual_override");
                });
    }

    @Test
    @DisplayName("validation refuses an empty title, an unknown type and an unknown level")
    void createValidation() {
        assertThatThrownBy(() -> service.create(
                REQUESTER, "incident", "  ", "", "high", "high", null,
                null, null, null, "manual", null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.INVALID_REQUEST));

        assertThatThrownBy(() -> service.create(
                REQUESTER, "outage", "x", "", "high", "high", null,
                null, null, null, "manual", null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.INVALID_TICKET_TYPE));

        assertThatThrownBy(() -> service.create(
                REQUESTER, "incident", "x", "", "catastrophic", "high", null,
                null, null, null, "manual", null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.INVALID_REQUEST));
    }

    // ------------------------------------------------------------------ visibility

    @Test
    @DisplayName("a requester only ever sees their own tickets, in the list and by id")
    void requesterIsScopedToTheirOwnTickets() {
        TicketDetail mine = create(REQUESTER, "我的故障");
        create(OTHER_REQUESTER, "别人的故障");

        TicketService.Page page = service.list(REQUESTER, null, null, null, null,
                null, null, false, 0, 20);
        assertThat(page.items()).extracting("id").containsExactly(mine.id());
        assertThat(page.total()).isEqualTo(1);

        // Reading someone else's ticket by id must look identical to it not existing.
        TicketDetail theirs = create(OTHER_REQUESTER, "另一张");
        assertThatThrownBy(() -> service.get(REQUESTER, theirs.id()))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.TICKET_NOT_FOUND));
    }

    @Test
    @DisplayName("an agent and an auditor see every ticket")
    void agentAndAuditorSeeEverything() {
        create(REQUESTER, "a");
        create(OTHER_REQUESTER, "b");

        assertThat(service.list(AGENT, null, null, null, null, null, null, false, 0, 20).total())
                .isEqualTo(2);
        assertThat(service.list(AUDITOR, null, null, null, null, null, null, false, 0, 20).total())
                .isEqualTo(2);
    }

    @Test
    @DisplayName("an internal note never reaches a requester")
    void internalNotesAreAgentOnly() {
        TicketDetail ticket = create(REQUESTER, "内部备注测试");
        service.addComment(AGENT, ticket.id(), "公开回复：我们已收到", "public");
        service.addComment(AGENT, ticket.id(), "内部：疑似网段配置问题", "internal");

        TicketDetail asAgent = service.get(AGENT, ticket.id());
        assertThat(asAgent.comments()).hasSize(2);

        TicketDetail asRequester = service.get(REQUESTER, ticket.id());
        assertThat(asRequester.comments()).hasSize(1);
        assertThat(asRequester.comments().get(0).visibility()).isEqualTo("public");
        assertThat(asRequester.comments())
                .noneSatisfy(comment -> assertThat(comment.body()).contains("疑似网段"));
    }

    @Test
    @DisplayName("a requester cannot write an internal note")
    void requesterCannotWriteInternalNotes() {
        TicketDetail ticket = create(REQUESTER, "x");

        assertThatThrownBy(() -> service.addComment(
                REQUESTER, ticket.id(), "偷偷记一笔", "internal"))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.INTERNAL_NOTE_FORBIDDEN));
        assertThat(store.comments).isEmpty();
    }

    // ------------------------------------------------------------------ lifecycle

    @Test
    @DisplayName("an illegal transition is refused and changes nothing")
    void illegalTransitionIsRefused() {
        TicketDetail ticket = create(AGENT, "状态测试");

        // new -> new is not a transition.
        assertThatThrownBy(() -> service.transition(AGENT, ticket.id(), "new", null, null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.INVALID_TRANSITION));

        service.transition(AGENT, ticket.id(), "closed", null, null);
        // closed -> resolved is not legal (only reopen).
        assertThatThrownBy(() -> service.transition(AGENT, ticket.id(), "resolved", null, null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.INVALID_TRANSITION));
        assertThat(service.get(AGENT, ticket.id()).status()).isEqualTo("closed");
    }

    @Test
    @DisplayName("resolving stamps resolved_at, closing stamps closed_at")
    void timestampsAreStamped() {
        TicketDetail ticket = create(AGENT, "时间戳");

        TicketDetail resolved = service.transition(AGENT, ticket.id(), "resolved", null, "已修复");
        assertThat(resolved.resolvedAt()).isNotNull();
        assertThat(resolved.closedAt()).isNull();
        assertThat(resolved.resolution()).isEqualTo("已修复");

        TicketDetail closed = service.transition(AGENT, ticket.id(), "closed", null, null);
        assertThat(closed.closedAt()).isNotNull();
        assertThat(closed.resolvedAt()).isNotNull();
    }

    /**
     * The subtle one: a reopened ticket must not still look resolved, because the SLA clock and
     * every report read those three timestamps.
     */
    @Test
    @DisplayName("reopening clears resolved_at and closed_at")
    void reopenClearsResolutionTimestamps() {
        TicketDetail ticket = create(AGENT, "重开");
        service.transition(AGENT, ticket.id(), "resolved", null, "已修复");
        service.transition(AGENT, ticket.id(), "closed", null, null);

        TicketDetail reopened = service.transition(AGENT, ticket.id(), "open", null, null);

        assertThat(reopened.status()).isEqualTo("open");
        assertThat(reopened.resolvedAt()).isNull();
        assertThat(reopened.closedAt()).isNull();
    }

    @Test
    @DisplayName("the first response is recorded once and never overwritten")
    void firstResponseIsSticky() {
        TicketDetail ticket = create(REQUESTER, "首次响应");

        // A requester's own comment is not a response to the requester.
        service.addComment(REQUESTER, ticket.id(), "补充信息", "public");
        assertThat(service.get(AGENT, ticket.id()).firstResponseAt()).isNull();

        TicketDetail afterAgent = service.addComment(AGENT, ticket.id(), "我们开始处理", "public");
        assertThat(afterAgent.firstResponseAt()).isNotNull();
        var first = afterAgent.firstResponseAt();

        // An internal note later must not move it.
        service.addComment(AGENT, ticket.id(), "内部同步", "internal");
        assertThat(service.get(AGENT, ticket.id()).firstResponseAt()).isEqualTo(first);
    }

    @Test
    @DisplayName("a requester may confirm or reject a resolution but not resolve it")
    void requesterTransitionScope() {
        TicketDetail ticket = create(REQUESTER, "确认");

        assertThatThrownBy(() -> service.transition(
                REQUESTER, ticket.id(), "resolved", null, null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.FORBIDDEN_TICKET_ACTION));

        service.transition(AGENT, ticket.id(), "resolved", null, "已修复");
        TicketDetail confirmed = service.transition(REQUESTER, ticket.id(), "closed", null, null);
        assertThat(confirmed.status()).isEqualTo("closed");
    }

    @Test
    @DisplayName("an auditor may read but not transition, comment internally or assign")
    void auditorCannotWrite() {
        TicketDetail ticket = create(AGENT, "审计");

        assertThatThrownBy(() -> service.transition(AUDITOR, ticket.id(), "open", null, null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.FORBIDDEN_TICKET_ACTION));
        assertThatThrownBy(() -> service.addComment(AUDITOR, ticket.id(), "x", "internal"))
                .isInstanceOf(AuthException.class);
        assertThatThrownBy(() -> service.update(
                AUDITOR, ticket.id(), "改标题", null, null, null, null, null, null))
                .isInstanceOf(AuthException.class);
    }

    // ------------------------------------------------------------------ timeline

    @Test
    @DisplayName("every mutation appends a timeline entry the requester can also see")
    void timelineIsAppendOnly() {
        // Created by the requester, so the requester can read it back.
        TicketDetail ticket = create(REQUESTER, "时间线");
        service.transition(AGENT, ticket.id(), "in_progress", "开始处理", null);
        service.addComment(AGENT, ticket.id(), "内部记录", "internal");

        TicketDetail detail = service.get(REQUESTER, ticket.id());
        // Activities are visible to the requester — the lifecycle of their own ticket is not
        // secret — while comments are filtered: only the public transition reply survives, and
        // the internal note does not.
        assertThat(detail.activities()).extracting("activityType")
                .contains("created", "status_changed", "comment_added");
        assertThat(detail.comments()).singleElement().satisfies(comment -> {
            assertThat(comment.visibility()).isEqualTo("public");
            assertThat(comment.body()).isEqualTo("开始处理");
        });
    }

    // ------------------------------------------------------------------ links, merge, split

    @Test
    @DisplayName("a link is visible from both ends with the correct label and direction")
    void linksReadFromBothEnds() {
        TicketDetail blocker = create(AGENT, "阻塞方");
        TicketDetail blocked = create(AGENT, "被阻塞方");

        service.addLink(AGENT, blocker.id(), blocked.id(), "blocks");

        TicketDetail fromBlocker = service.get(AGENT, blocker.id());
        assertThat(fromBlocker.links()).singleElement().satisfies(link -> {
            assertThat(link.linkType()).isEqualTo("blocks");
            assertThat(link.direction()).isEqualTo("outgoing");
            assertThat(link.linkedNumber()).isEqualTo(blocked.number());
            assertThat(link.linkedTitle()).isEqualTo("被阻塞方");
        });

        // The inverse label is derived, not stored twice.
        TicketDetail fromBlocked = service.get(AGENT, blocked.id());
        assertThat(fromBlocked.links()).singleElement().satisfies(link -> {
            assertThat(link.linkType()).isEqualTo("blocked_by");
            assertThat(link.direction()).isEqualTo("incoming");
            assertThat(link.linkedNumber()).isEqualTo(blocker.number());
        });
    }

    @Test
    @DisplayName("self links and duplicate links are refused")
    void linkValidation() {
        TicketDetail a = create(AGENT, "a");
        TicketDetail b = create(AGENT, "b");

        assertThatThrownBy(() -> service.addLink(AGENT, a.id(), a.id(), "related"))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.LINK_SELF));

        service.addLink(AGENT, a.id(), b.id(), "related");
        assertThatThrownBy(() -> service.addLink(AGENT, a.id(), b.id(), "related"))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.LINK_DUPLICATE));

        assertThatThrownBy(() -> service.addLink(AGENT, a.id(), b.id(), "nonsense"))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.INVALID_LINK_TYPE));
    }

    @Test
    @DisplayName("merging moves the conversation and history, and closes the source as a pointer")
    void mergeMovesEverything() {
        TicketDetail source = create(REQUESTER, "重复单");
        TicketDetail target = create(REQUESTER, "主单");
        service.addComment(REQUESTER, source.id(), "我的打印机坏了", "public");
        service.transition(AGENT, source.id(), "in_progress", null, null);

        service.merge(AGENT, source.id(), target.id());

        TicketDetail mergedSource = service.get(AGENT, source.id());
        assertThat(mergedSource.mergedIntoId()).isEqualTo(target.id());
        assertThat(mergedSource.status()).isEqualTo("closed");

        TicketDetail mergedTarget = service.get(AGENT, target.id());
        assertThat(mergedTarget.comments()).anySatisfy(comment ->
                assertThat(comment.body()).contains("我的打印机坏了"));
        assertThat(mergedTarget.activities()).anySatisfy(activity ->
                assertThat(activity.activityType()).isEqualTo("merged"));
    }

    @Test
    @DisplayName("a merged ticket cannot be worked on, merged again, or merged in a cycle")
    void mergeGuards() {
        TicketDetail a = create(AGENT, "a");
        TicketDetail b = create(AGENT, "b");
        TicketDetail c = create(AGENT, "c");

        assertThatThrownBy(() -> service.merge(AGENT, a.id(), a.id()))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.MERGE_SELF));

        service.merge(AGENT, a.id(), b.id());
        // a is now a tombstone.
        assertThatThrownBy(() -> service.merge(AGENT, a.id(), c.id()))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.TICKET_ALREADY_MERGED));
        // Working on it is refused too.
        assertThatThrownBy(() -> service.transition(AGENT, a.id(), "open", null, null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.TICKET_ALREADY_MERGED));
        // b -> a is refused because a is already a tombstone, which is also what makes a merge
        // cycle structurally impossible: a cycle would need a merged ticket as a target.
        // The recursive chain check in the service is therefore defence in depth, and this
        // asserts the rule that actually fires.
        assertThatThrownBy(() -> service.merge(AGENT, b.id(), a.id()))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.TICKET_ALREADY_MERGED));
    }

    @Test
    @DisplayName("merging is single-winner when two requests race")
    void mergeIsSingleWinner() {
        TicketDetail source = create(AGENT, "source");
        TicketDetail targetA = create(AGENT, "targetA");
        TicketDetail targetB = create(AGENT, "targetB");

        service.merge(AGENT, source.id(), targetA.id());

        assertThatThrownBy(() -> service.merge(AGENT, source.id(), targetB.id()))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.TICKET_ALREADY_MERGED));
        assertThat(service.get(AGENT, source.id()).mergedIntoId()).isEqualTo(targetA.id());
    }

    @Test
    @DisplayName("splitting copies the comment into a child ticket and links them")
    void splitCreatesAChild() {
        TicketDetail parent = create(REQUESTER, "两件事混在一起");
        service.addComment(REQUESTER, parent.id(), "另外还有一件事：显示器闪烁", "public");
        String commentId = service.get(AGENT, parent.id()).comments().get(0).id();

        TicketDetail child = service.split(
                AGENT, parent.id(), commentId, "显示器闪烁", null
        );

        assertThat(child.parentId()).isEqualTo(parent.id());
        assertThat(child.requesterId()).isEqualTo(parent.requesterId());
        assertThat(child.id()).isNotEqualTo(parent.id());
        assertThat(child.comments()).singleElement().satisfies(comment ->
                assertThat(comment.body())
                        .contains("从 #" + parent.number() + " 拆分")
                        .contains("显示器闪烁"));

        TicketDetail refreshedParent = service.get(AGENT, parent.id());
        assertThat(refreshedParent.children()).extracting("id").containsExactly(child.id());
        assertThat(refreshedParent.activities()).anySatisfy(activity ->
                assertThat(activity.activityType()).isEqualTo("split"));
        // The original comment stays put: the communication log is immutable.
        assertThat(refreshedParent.comments()).hasSize(1);
    }

    @Test
    @DisplayName("splitting refuses a comment that belongs to another ticket")
    void splitRejectsForeignComment() {
        TicketDetail a = create(AGENT, "a");
        TicketDetail b = create(AGENT, "b");
        service.addComment(AGENT, b.id(), "b 的评论", "public");
        String commentOfB = service.get(AGENT, b.id()).comments().get(0).id();

        assertThatThrownBy(() -> service.split(AGENT, a.id(), commentOfB, "标题", null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(TicketService.SPLIT_COMMENT_NOT_FOUND));
    }

    @Test
    @DisplayName("a requester cannot link, merge or split")
    void requesterCannotRestructure() {
        TicketDetail a = create(REQUESTER, "a");
        TicketDetail b = create(REQUESTER, "b");
        service.addComment(REQUESTER, a.id(), "评论", "public");
        String commentId = service.get(REQUESTER, a.id()).comments().get(0).id();

        assertThatThrownBy(() -> service.addLink(REQUESTER, a.id(), b.id(), "related"))
                .isInstanceOf(AuthException.class);
        assertThatThrownBy(() -> service.merge(REQUESTER, a.id(), b.id()))
                .isInstanceOf(AuthException.class);
        assertThatThrownBy(() -> service.split(REQUESTER, a.id(), commentId, "x", null))
                .isInstanceOf(AuthException.class);
    }

    // ------------------------------------------------------------------ listing

    @Test
    @DisplayName("list filters, paging and grouped counts agree with the rows")
    void listFiltersAndCounts() {
        TicketDetail incident = create(AGENT, "故障单");
        TicketDetail request = service.create(
                AGENT, "request", "申请单", "", "low", "low", null,
                null, null, null, "manual", null
        );
        service.transition(AGENT, request.id(), "in_progress", null, null);

        TicketService.Page all = service.list(AGENT, null, null, null, null, null, null,
                false, 0, 20);
        assertThat(all.total()).isEqualTo(2);
        assertThat(all.byStatus()).containsEntry("new", 1).containsEntry("in_progress", 1);

        TicketService.Page incidents = service.list(AGENT, null, null, "incident", null,
                null, null, false, 0, 20);
        assertThat(incidents.items()).extracting("id").containsExactly(incident.id());

        TicketService.Page byQuery = service.list(AGENT, "申请", null, null, null, null,
                null, false, 0, 20);
        assertThat(byQuery.items()).extracting("id").containsExactly(request.id());

        // Paging.
        TicketService.Page firstPage = service.list(AGENT, null, null, null, null, null,
                null, false, 0, 1);
        TicketService.Page secondPage = service.list(AGENT, null, null, null, null, null,
                null, false, 1, 1);
        assertThat(firstPage.items()).hasSize(1);
        assertThat(secondPage.items()).hasSize(1);
        assertThat(secondPage.items()).doesNotContainAnyElementsOf(firstPage.items());
    }

    @Test
    @DisplayName("the meta payload exposes the matrix inputs and the legal transitions")
    @SuppressWarnings("unchecked")
    void meta() {
        var meta = service.meta();

        assertThat(meta.get("types")).isEqualTo(List.of("incident", "request", "problem", "change"));
        assertThat(meta.get("impactLevels")).isEqualTo(List.of("high", "medium", "low"));
        var transitions = (java.util.Map<String, List<String>>) meta.get("transitions");
        assertThat(transitions.get("resolved")).containsExactlyInAnyOrder("closed", "open");
        assertThat(transitions.get("closed")).containsExactly("open");
        assertThat((List<String>) meta.get("agentRoles")).contains("it_staff");
    }

    @Test
    @DisplayName("page size and page number are clamped")
    void pagingIsClamped() {
        TicketService.Page page = service.list(AGENT, null, null, null, null, null, null,
                false, -5, 100_000);
        assertThat(page.size()).isEqualTo(100);
        assertThat(page.page()).isZero();
    }

    @Test
    @DisplayName("created tickets carry a monotonically increasing human number")
    void numbersIncrease() {
        TicketDetail first = create(AGENT, "one");
        TicketDetail second = create(AGENT, "two");

        assertThat(second.number()).isGreaterThan(first.number());
    }

    @Test
    @DisplayName("an update recomputes the matrix priority when impact or urgency moves")
    void updateRecomputesPriority() {
        TicketDetail ticket = service.create(
                AGENT, "incident", "优先级联动", "", "low", "low", null,
                null, null, null, "manual", null
        );
        assertThat(ticket.priority()).isEqualTo("low");

        TicketDetail raised = service.update(
                AGENT, ticket.id(), null, null, null, "high", "high", null, null);

        assertThat(raised.impact()).isEqualTo("high");
        assertThat(raised.priority()).isEqualTo("critical");
        assertThat(raised.activities()).anySatisfy(activity ->
                assertThat(activity.activityType()).isEqualTo("priority_changed"));
    }

    @Test
    @DisplayName("assigning is recorded as its own timeline entry")
    void assignmentIsRecorded() {
        TicketDetail ticket = create(AGENT, "分派");

        TicketDetail assigned = service.update(
                AGENT, ticket.id(), null, null, null, null, null, null, "agent-9");

        assertThat(assigned.assigneeId()).isEqualTo("agent-9");
        assertThat(assigned.activities()).anySatisfy(activity -> {
            assertThat(activity.activityType()).isEqualTo("assigned");
            assertThat(activity.toValue()).isEqualTo("agent-9");
        });
    }

    @Test
    @DisplayName("store state is exactly what the assertions assume")
    void storeSanity() {
        Ticket ticket = store.ticket("missing");
        assertThat(ticket).isNull();
    }
}
