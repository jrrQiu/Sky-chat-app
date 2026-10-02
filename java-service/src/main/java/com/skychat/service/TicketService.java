package com.skychat.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.domain.ticket.Ticket;
import com.skychat.domain.ticket.TicketActivity;
import com.skychat.domain.ticket.TicketComment;
import com.skychat.domain.ticket.TicketLink;
import com.skychat.domain.ticket.TicketLinkType;
import com.skychat.domain.ticket.TicketPermissions;
import com.skychat.domain.ticket.TicketPriority;
import com.skychat.domain.ticket.TicketStatus;
import com.skychat.domain.ticket.TicketType;
import com.skychat.dto.TicketActivityView;
import com.skychat.dto.TicketCommentView;
import com.skychat.dto.TicketDetail;
import com.skychat.dto.TicketLinkView;
import com.skychat.dto.TicketSummary;
import com.skychat.mapper.TicketActivityMapper;
import com.skychat.mapper.TicketActivityRow;
import com.skychat.mapper.TicketCommentMapper;
import com.skychat.mapper.TicketLinkMapper;
import com.skychat.mapper.TicketMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * Ticket lifecycle, authorization and timeline.
 *
 * <p>Everything that must not be trusted from a client is enforced here rather than in the
 * controller: the status machine, the impact x urgency matrix, agent-versus-requester
 * abilities, and the internal-note visibility filter.</p>
 */
@Service
public class TicketService {
    public static final String TICKET_NOT_FOUND = "TICKET_NOT_FOUND";
    public static final String INVALID_TRANSITION = "INVALID_TRANSITION";
    public static final String INVALID_TICKET_TYPE = "INVALID_TICKET_TYPE";
    public static final String INVALID_TICKET_STATUS = "INVALID_TICKET_STATUS";
    public static final String INVALID_LINK_TYPE = "INVALID_LINK_TYPE";
    public static final String INVALID_REQUEST = "INVALID_REQUEST";
    public static final String FORBIDDEN_TICKET_ACTION = "FORBIDDEN_TICKET_ACTION";
    public static final String INTERNAL_NOTE_FORBIDDEN = "INTERNAL_NOTE_FORBIDDEN";
    public static final String TICKET_ALREADY_MERGED = "TICKET_ALREADY_MERGED";
    public static final String MERGE_SELF = "MERGE_SELF";
    public static final String MERGE_CYCLE = "MERGE_CYCLE";
    public static final String LINK_SELF = "LINK_SELF";
    public static final String LINK_DUPLICATE = "LINK_DUPLICATE";
    public static final String SPLIT_COMMENT_NOT_FOUND = "SPLIT_COMMENT_NOT_FOUND";

    private static final int MAX_TITLE = 300;
    private static final int MAX_BODY = 20_000;
    private static final int MAX_PAGE_SIZE = 100;
    private static final int ACTIVITY_LIMIT = 500;

    /** Who is asking. Roles come from the signed JWT, never from the request body. */
    public record Caller(String userId, List<String> roles) {
        public boolean isAgent() {
            return TicketPermissions.isAgent(roles);
        }

        public boolean canViewAll() {
            return TicketPermissions.canViewAll(roles);
        }
    }

    public record Page(List<TicketSummary> items, int total, int page, int size,
                       Map<String, Integer> byStatus, Map<String, Integer> byPriority) {
    }

    public record MergeResult(TicketDetail source, TicketDetail target) {
    }

    private final TicketMapper ticketMapper;
    private final TicketCommentMapper commentMapper;
    private final TicketActivityMapper activityMapper;
    private final TicketLinkMapper linkMapper;
    private final ObjectMapper objectMapper;

    public TicketService(
            TicketMapper ticketMapper,
            TicketCommentMapper commentMapper,
            TicketActivityMapper activityMapper,
            TicketLinkMapper linkMapper,
            ObjectMapper objectMapper
    ) {
        this.ticketMapper = ticketMapper;
        this.commentMapper = commentMapper;
        this.activityMapper = activityMapper;
        this.linkMapper = linkMapper;
        this.objectMapper = objectMapper;
    }

    // ------------------------------------------------------------------ reads

    public Page list(
            Caller caller,
            String query,
            String status,
            String type,
            String priority,
            String assigneeId,
            String requesterId,
            boolean includeMerged,
            int page,
            int size
    ) {
        String normalizedStatus = normalizeOptionalStatus(status);
        String normalizedType = normalizeOptionalType(type);
        String normalizedPriority = normalizeOptionalPriority(priority);
        // The security predicate: a caller who may not see everything is pinned to their own
        // tickets in SQL, so a filter cannot be used to read someone else's queue.
        String visibleToRequester = caller.canViewAll() ? null : caller.userId();

        int safeSize = Math.max(1, Math.min(size <= 0 ? 20 : size, MAX_PAGE_SIZE));
        int safePage = Math.max(0, page);
        List<Ticket> rows = ticketMapper.search(
                blankToNull(query),
                normalizedStatus,
                normalizedType,
                normalizedPriority,
                blankToNull(assigneeId),
                blankToNull(requesterId),
                visibleToRequester,
                includeMerged,
                safeSize,
                safePage * safeSize
        );
        int total = ticketMapper.countSearch(
                blankToNull(query), normalizedStatus, normalizedType, normalizedPriority,
                blankToNull(assigneeId), blankToNull(requesterId), visibleToRequester,
                includeMerged
        );
        return new Page(
                rows.stream().map(TicketSummary::of).toList(),
                total,
                safePage,
                safeSize,
                toCountMap(ticketMapper.countByStatus(visibleToRequester)),
                toCountMap(ticketMapper.countByPriority(visibleToRequester))
        );
    }

    public TicketDetail get(Caller caller, String id) {
        return detail(requireVisible(caller, id), caller);
    }

    /** The options the UI needs, including the legal transitions per status. */
    public Map<String, Object> meta() {
        Map<String, Object> transitions = new LinkedHashMap<>();
        for (TicketStatus status : TicketStatus.values()) {
            transitions.put(
                    status.value(),
                    status.allowedNext().stream().map(TicketStatus::value).toList()
            );
        }
        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("types", TicketType.valuesAsStrings());
        meta.put("statuses", TicketStatus.valuesAsStrings());
        meta.put("priorities", TicketPriority.valuesAsStrings());
        meta.put("impactLevels", TicketPriority.Level.valuesAsStrings());
        meta.put("urgencyLevels", TicketPriority.Level.valuesAsStrings());
        meta.put("linkTypes", TicketLinkType.valuesAsStrings());
        meta.put("agentRoles", TicketPermissions.agentRoles());
        meta.put("transitions", transitions);
        return meta;
    }

    // ------------------------------------------------------------------ writes

    public TicketDetail create(
            Caller caller,
            String type,
            String title,
            String description,
            String impact,
            String urgency,
            String requestedPriority,
            String assigneeId,
            String parentId,
            String requesterId,
            String source,
            String conversationId
    ) {
        TicketType ticketType = parseType(type);
        String cleanTitle = requireText(title, "title", MAX_TITLE);
        String cleanImpact = parseLevel(impact).value();
        String cleanUrgency = parseLevel(urgency).value();

        // Only an agent may file on someone else's behalf.
        String effectiveRequester = caller.userId();
        if (requesterId != null && !requesterId.isBlank()
                && !requesterId.equals(caller.userId())) {
            requireAgent(caller);
            effectiveRequester = requesterId.trim();
        }
        if (assigneeId != null && !assigneeId.isBlank()) {
            requireAgent(caller);
        }
        if (requestedPriority != null && !requestedPriority.isBlank()) {
            // A requester cannot set priority; an agent may override the matrix, and the
            // override is recorded so it is visible that the matrix was not followed.
            requireAgent(caller);
        }

        LocalDateTime now = LocalDateTime.now();
        Ticket ticket = new Ticket();
        ticket.setId(UUID.randomUUID().toString());
        ticket.setNumber(ticketMapper.nextNumber());
        ticket.setType(ticketType.value());
        ticket.setStatus(TicketStatus.NEW.value());
        ticket.setImpact(cleanImpact);
        ticket.setUrgency(cleanUrgency);
        ticket.setPriority(requestedPriority == null || requestedPriority.isBlank()
                ? TicketPriority.derive(cleanImpact, cleanUrgency)
                : normalizePriority(requestedPriority));
        ticket.setTitle(cleanTitle);
        ticket.setDescription(description == null ? "" : description.trim());
        ticket.setRequesterId(effectiveRequester);
        ticket.setAssigneeId(blankToNull(assigneeId));
        ticket.setParentId(blankToNull(parentId));
        ticket.setSource(normalizeSource(source));
        ticket.setConversationId(blankToNull(conversationId));
        ticket.setCreatedBy(caller.userId());
        ticket.setCreatedAt(now);
        ticket.setUpdatedAt(now);
        ticketMapper.insert(ticket);

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("type", ticket.getType());
        detail.put("priority", ticket.getPriority());
        detail.put("impact", ticket.getImpact());
        detail.put("urgency", ticket.getUrgency());
        if (requestedPriority != null && !requestedPriority.isBlank()) {
            detail.put("prioritySource", "manual_override");
        } else {
            detail.put("prioritySource", "impact_urgency_matrix");
        }
        if (ticket.getParentId() != null) {
            detail.put("parentId", ticket.getParentId());
        }
        record(ticket.getId(), caller.userId(), TicketActivity.CREATED, null,
                ticket.getStatus(), detail);

        if (ticket.getAssigneeId() != null) {
            record(ticket.getId(), caller.userId(), TicketActivity.ASSIGNED, null,
                    ticket.getAssigneeId(), Map.of());
        }
        return detail(ticket, caller);
    }

    public TicketDetail update(
            Caller caller,
            String id,
            String title,
            String description,
            String type,
            String impact,
            String urgency,
            String priority,
            String assigneeId
    ) {
        requireAgent(caller);
        Ticket ticket = requireVisible(caller, id);
        requireNotMerged(ticket);

        String newTitle = title == null ? null : requireText(title, "title", MAX_TITLE);
        String newType = type == null ? null : parseType(type).value();
        String newImpact = impact == null ? null : parseLevel(impact).value();
        String newUrgency = urgency == null ? null : parseLevel(urgency).value();
        String newPriority = priority == null ? null : normalizePriority(priority);
        String newAssignee = assigneeId == null ? null : blankToNull(assigneeId);

        // Snapshot the before-values. Comparing against the loaded object later would be
        // fragile: nothing guarantees the mapper returned a detached copy rather than a row
        // the write then mutates, and a cached mapper would silently make every comparison
        // false and drop the timeline entries.
        String beforeTitle = ticket.getTitle();
        String beforeDescription = ticket.getDescription();
        String beforeType = ticket.getType();
        String beforeImpact = ticket.getImpact();
        String beforeUrgency = ticket.getUrgency();
        String beforePriority = ticket.getPriority();
        String beforeAssignee = ticket.getAssigneeId();

        // Recompute the matrix priority when impact or urgency moved and no explicit priority
        // was supplied, so the stored priority keeps matching its inputs.
        boolean matrixInputsChanged = (newImpact != null && !newImpact.equals(beforeImpact))
                || (newUrgency != null && !newUrgency.equals(beforeUrgency));
        if (newPriority == null && matrixInputsChanged) {
            newPriority = TicketPriority.derive(
                    newImpact == null ? beforeImpact : newImpact,
                    newUrgency == null ? beforeUrgency : newUrgency
            );
        }

        LocalDateTime now = LocalDateTime.now();
        ticketMapper.updateFields(
                id, newTitle, description, newType, newImpact, newUrgency, newPriority,
                newAssignee, now
        );

        List<String> changed = new ArrayList<>();
        if (newTitle != null && !newTitle.equals(beforeTitle)) {
            changed.add("title");
        }
        if (description != null && !description.equals(beforeDescription)) {
            changed.add("description");
        }
        if (newType != null && !newType.equals(beforeType)) {
            changed.add("type");
            record(id, caller.userId(), TicketActivity.FIELDS_UPDATED, beforeType,
                    newType, Map.of("field", "type"));
        }
        if (newImpact != null && !newImpact.equals(beforeImpact)) {
            changed.add("impact");
        }
        if (newUrgency != null && !newUrgency.equals(beforeUrgency)) {
            changed.add("urgency");
        }
        if (newPriority != null && !newPriority.equals(beforePriority)) {
            changed.add("priority");
            Map<String, Object> detail = new LinkedHashMap<>();
            detail.put("field", "priority");
            detail.put("impact", newImpact == null ? beforeImpact : newImpact);
            detail.put("urgency", newUrgency == null ? beforeUrgency : newUrgency);
            record(id, caller.userId(), TicketActivity.PRIORITY_CHANGED, beforePriority,
                    newPriority, detail);
        }
        if (newAssignee != null && !newAssignee.equals(beforeAssignee)) {
            changed.add("assigneeId");
            record(id, caller.userId(), TicketActivity.ASSIGNED, beforeAssignee,
                    newAssignee, Map.of());
        }
        if (!changed.isEmpty() && changed.stream().allMatch(
                field -> List.of("title", "description", "impact", "urgency").contains(field))) {
            record(id, caller.userId(), TicketActivity.FIELDS_UPDATED, null, null,
                    Map.of("fields", changed));
        } else if (changed.isEmpty()) {
            // Nothing changed: do not manufacture a timeline entry.
            ticketMapper.touch(id, now);
        }
        return detail(requireVisible(caller, id), caller);
    }

    public TicketDetail transition(
            Caller caller,
            String id,
            String status,
            String comment,
            String resolution
    ) {
        Ticket ticket = requireVisible(caller, id);
        requireNotMerged(ticket);

        TicketStatus current = TicketStatus.parse(ticket.getStatus());
        TicketStatus next = parseStatus(status);
        if (current == next) {
            throw new AuthException(INVALID_TRANSITION, "工单已处于该状态");
        }
        if (!current.canTransitionTo(next)) {
            throw new AuthException(
                    INVALID_TRANSITION,
                    "不允许从 " + current.label() + " 变更为 " + next.label()
            );
        }
        // A requester may only confirm or reject a resolution; everything else is agent work.
        if (!caller.isAgent() && !current.requesterMayTransitionTo(next)) {
            throw new AuthException(FORBIDDEN_TICKET_ACTION, "只有处理人可以变更工单状态");
        }

        LocalDateTime now = LocalDateTime.now();
        String cleanResolution = resolution == null ? null : blankToNull(resolution);
        if (next == TicketStatus.RESOLVED && cleanResolution == null) {
            cleanResolution = ticket.getResolution();
        }
        // Reopening must clear the resolution timestamps: leaving them set would make a reopened
        // ticket look resolved to the SLA clock (S2) and to reporting.
        LocalDateTime resolvedAt = next == TicketStatus.RESOLVED
                ? (ticket.getResolvedAt() == null ? now : ticket.getResolvedAt())
                : null;
        LocalDateTime closedAt = next == TicketStatus.CLOSED
                ? (ticket.getClosedAt() == null ? now : ticket.getClosedAt())
                : null;
        if (next == TicketStatus.CLOSED && resolvedAt == null) {
            resolvedAt = ticket.getResolvedAt();
        }

        LocalDateTime firstResponse = null;
        if (caller.isAgent() && ticket.getFirstResponseAt() == null && next.isActive()) {
            // An agent moving the ticket forward is the first response from the requester's
            // point of view when no public reply has happened yet.
            firstResponse = now;
        }

        ticketMapper.updateStatus(id, next.value(), cleanResolution, firstResponse,
                resolvedAt, closedAt, now);

        Map<String, Object> detail = new LinkedHashMap<>();
        if (cleanResolution != null && next == TicketStatus.RESOLVED) {
            detail.put("resolution", cleanResolution);
        }
        record(id, caller.userId(), TicketActivity.STATUS_CHANGED, current.value(), next.value(),
                detail);
        if (comment != null && !comment.isBlank()) {
            // A transition comment is always a public reply: the requester is the audience for
            // "we are looking into it". An agent wanting an internal note posts one explicitly
            // through addComment, which keeps the two intents distinguishable.
            addComment(caller, id, comment, TicketComment.VISIBILITY_PUBLIC);
        }
        return detail(requireVisible(caller, id), caller);
    }

    public TicketDetail addComment(
            Caller caller,
            String id,
            String body,
            String visibility
    ) {
        Ticket ticket = requireVisible(caller, id);
        requireNotMerged(ticket);
        String cleanBody = requireText(body, "body", MAX_BODY);
        String cleanVisibility = normalizeVisibility(visibility);
        if (TicketComment.VISIBILITY_INTERNAL.equals(cleanVisibility) && !caller.isAgent()) {
            throw new AuthException(INTERNAL_NOTE_FORBIDDEN, "只有处理人可以添加内部备注");
        }

        TicketComment comment = new TicketComment();
        comment.setId(UUID.randomUUID().toString());
        comment.setTicketId(id);
        comment.setAuthorId(caller.userId());
        comment.setAuthorRoles(String.join(",", caller.roles() == null ? List.of() : caller.roles()));
        comment.setVisibility(cleanVisibility);
        comment.setBody(cleanBody);
        comment.setCreatedAt(LocalDateTime.now());
        commentMapper.insert(comment);

        // The first *public agent* reply is what "first response" means for the SLA clock; an
        // internal note is not a response to the requester.
        if (caller.isAgent() && TicketComment.VISIBILITY_PUBLIC.equals(cleanVisibility)) {
            ticketMapper.markFirstResponse(id, comment.getCreatedAt());
        }
        record(id, caller.userId(), TicketActivity.COMMENT_ADDED, null, cleanVisibility,
                Map.of("commentId", comment.getId()));
        ticketMapper.touch(id, comment.getCreatedAt());
        return detail(requireVisible(caller, id), caller);
    }

    public TicketDetail addLink(
            Caller caller,
            String id,
            String targetId,
            String linkType
    ) {
        requireAgent(caller);
        Ticket ticket = requireVisible(caller, id);
        String cleanTarget = requireText(targetId, "targetId", 64);
        if (cleanTarget.equals(id)) {
            throw new AuthException(LINK_SELF, "不能把工单关联到它自己");
        }
        Ticket target = requireVisible(caller, cleanTarget);
        TicketLinkType type = parseLinkType(linkType);
        if (linkMapper.exists(id, cleanTarget, type.value()) > 0) {
            throw new AuthException(LINK_DUPLICATE, "该关联已存在");
        }

        TicketLink link = new TicketLink();
        link.setId(UUID.randomUUID().toString());
        link.setTicketId(id);
        link.setLinkedTicketId(cleanTarget);
        link.setLinkType(type.value());
        link.setCreatedBy(caller.userId());
        link.setCreatedAt(LocalDateTime.now());
        linkMapper.insert(link);

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("linkType", type.value());
        detail.put("targetId", cleanTarget);
        detail.put("targetNumber", target.getNumber());
        record(id, caller.userId(), TicketActivity.LINKED, null, type.value(), detail);
        record(cleanTarget, caller.userId(), TicketActivity.LINKED, null, type.inverse().value(),
                Map.of("linkType", type.inverse().value(), "targetId", id,
                        "targetNumber", ticket.getNumber()));
        ticketMapper.touch(id, LocalDateTime.now());
        ticketMapper.touch(cleanTarget, LocalDateTime.now());
        return detail(requireVisible(caller, id), caller);
    }

    public TicketDetail removeLink(Caller caller, String id, String linkId) {
        requireAgent(caller);
        requireVisible(caller, id);
        TicketLink link = linkMapper.findById(linkId);
        if (link == null || !link.getTicketId().equals(id)) {
            throw new AuthException(TicketActivity.UNLINKED, "关联不存在");
        }
        linkMapper.delete(linkId, id);
        record(id, caller.userId(), TicketActivity.UNLINKED, link.getLinkType(), null,
                Map.of("targetId", link.getLinkedTicketId()));
        ticketMapper.touch(id, LocalDateTime.now());
        return detail(requireVisible(caller, id), caller);
    }

    /**
     * Merges {@code sourceId} into {@code targetId}.
     *
     * <p>The source keeps its row as a tombstone pointing at the target; its communication log
     * and history move across so the merged conversation stays readable in one place.</p>
     */
    public MergeResult merge(Caller caller, String sourceId, String targetId) {
        requireAgent(caller);
        Ticket source = requireVisible(caller, sourceId);
        String cleanTarget = requireText(targetId, "targetId", 64);
        if (cleanTarget.equals(sourceId)) {
            throw new AuthException(MERGE_SELF, "不能把工单合并到它自己");
        }
        Ticket target = requireVisible(caller, cleanTarget);
        requireNotMerged(source);
        requireNotMerged(target);
        if (linkMapper.mergeChainContains(cleanTarget, sourceId) > 0) {
            throw new AuthException(MERGE_CYCLE, "合并会形成循环");
        }

        LocalDateTime now = LocalDateTime.now();
        if (ticketMapper.markMerged(sourceId, cleanTarget, now) == 0) {
            // Lost a race with another merge.
            throw new AuthException(TICKET_ALREADY_MERGED, "工单已被合并");
        }
        commentMapper.moveToTicket(sourceId, cleanTarget);
        activityMapper.moveToTicket(sourceId, cleanTarget);
        linkMapper.repointIncoming(sourceId, cleanTarget);
        linkMapper.repointOutgoing(sourceId, cleanTarget);

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("targetId", cleanTarget);
        detail.put("targetNumber", target.getNumber());
        record(cleanTarget, caller.userId(), TicketActivity.MERGED, sourceId,
                String.valueOf(source.getNumber()), detail);

        return new MergeResult(
                detail(requireVisible(caller, sourceId), caller),
                detail(requireVisible(caller, cleanTarget), caller)
        );
    }

    /**
     * Splits a new ticket out of one comment.
     *
     * <p>The comment itself does not move — the communication log is immutable — so the new
     * ticket gets a copy that references where it came from.</p>
     */
    public TicketDetail split(
            Caller caller,
            String id,
            String commentId,
            String title,
            String description
    ) {
        requireAgent(caller);
        Ticket source = requireVisible(caller, id);
        requireNotMerged(source);
        TicketComment comment = commentMapper.findById(commentId);
        if (comment == null || !comment.getTicketId().equals(id)) {
            throw new AuthException(SPLIT_COMMENT_NOT_FOUND, "评论不存在或不属于该工单");
        }

        LocalDateTime now = LocalDateTime.now();
        Ticket created = new Ticket();
        created.setId(UUID.randomUUID().toString());
        created.setNumber(ticketMapper.nextNumber());
        created.setType(source.getType());
        created.setStatus(TicketStatus.NEW.value());
        created.setImpact(source.getImpact());
        created.setUrgency(source.getUrgency());
        created.setPriority(TicketPriority.derive(source.getImpact(), source.getUrgency()));
        created.setTitle(requireText(title, "title", MAX_TITLE));
        created.setDescription(description == null ? "" : description.trim());
        created.setRequesterId(source.getRequesterId());
        created.setParentId(source.getId());
        created.setSource(source.getSource());
        created.setConversationId(source.getConversationId());
        created.setCreatedBy(caller.userId());
        created.setCreatedAt(now);
        created.setUpdatedAt(now);
        ticketMapper.insert(created);

        TicketComment copy = new TicketComment();
        copy.setId(UUID.randomUUID().toString());
        copy.setTicketId(created.getId());
        copy.setAuthorId(comment.getAuthorId());
        copy.setAuthorRoles(comment.getAuthorRoles());
        copy.setVisibility(comment.getVisibility());
        copy.setBody("（从 #" + source.getNumber() + " 拆分）\n" + comment.getBody());
        copy.setCreatedAt(now);
        commentMapper.insert(copy);

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("parentId", source.getId());
        detail.put("parentNumber", source.getNumber());
        detail.put("commentId", commentId);
        record(created.getId(), caller.userId(), TicketActivity.CREATED, null,
                TicketStatus.NEW.value(), detail);

        TicketLink link = new TicketLink();
        link.setId(UUID.randomUUID().toString());
        link.setTicketId(source.getId());
        link.setLinkedTicketId(created.getId());
        link.setLinkType(TicketLinkType.RELATED.value());
        link.setCreatedBy(caller.userId());
        link.setCreatedAt(now);
        linkMapper.insert(link);

        record(source.getId(), caller.userId(), TicketActivity.SPLIT, null,
                String.valueOf(created.getNumber()),
                Map.of("newTicketId", created.getId(), "newNumber", created.getNumber(),
                        "commentId", commentId));
        ticketMapper.touch(source.getId(), now);
        return detail(created, caller);
    }

    // ------------------------------------------------------------------ internals

    private TicketDetail detail(Ticket ticket, Caller caller) {
        boolean includeInternal = caller.isAgent() || TicketPermissions.isAuditorOnly(caller.roles());
        List<TicketCommentView> comments = commentMapper
                .findByTicket(ticket.getId(), includeInternal)
                .stream()
                .map(TicketCommentView::of)
                .toList();
        List<TicketActivityView> activities = activityMapper
                .findByTicket(ticket.getId(), ACTIVITY_LIMIT)
                .stream()
                .map(row -> new TicketActivityView(
                        row.id(),
                        row.ticketId(),
                        row.actorId(),
                        row.activityType(),
                        row.fromValue(),
                        row.toValue(),
                        parseDetail(row.detailJson()),
                        row.createdAt()
                ))
                .toList();
        List<TicketLinkView> links = links(ticket.getId());
        List<TicketSummary> children = ticketMapper.findChildren(ticket.getId()).stream()
                .map(TicketSummary::of)
                .toList();
        return TicketDetail.of(ticket, comments, activities, links, children);
    }

    private List<TicketLinkView> links(String ticketId) {
        List<TicketLinkView> views = new ArrayList<>();
        for (TicketLink link : linkMapper.findOutgoing(ticketId)) {
            views.add(view(link, link.getLinkedTicketId(), link.getLinkType(),
                    TicketLinkView.OUTGOING));
        }
        for (TicketLink link : linkMapper.findIncoming(ticketId)) {
            // The other side declared it, so this ticket reads the inverse label.
            views.add(view(link, link.getTicketId(),
                    inverseOf(link.getLinkType()), TicketLinkView.INCOMING));
        }
        return views;
    }

    private TicketLinkView view(TicketLink link, String otherId, String shownType, String direction) {
        Ticket other = ticketMapper.findById(otherId);
        return new TicketLinkView(
                link.getId(),
                link.getTicketId(),
                otherId,
                shownType,
                direction,
                other == null ? null : other.getNumber(),
                other == null ? null : other.getTitle(),
                other == null ? null : other.getStatus(),
                link.getCreatedBy(),
                link.getCreatedAt()
        );
    }

    private static String inverseOf(String linkType) {
        try {
            return TicketLinkType.parse(linkType).inverse().value();
        } catch (IllegalArgumentException error) {
            return linkType;
        }
    }

    /**
     * Loads a ticket the caller is allowed to see, or reports it as missing.
     *
     * <p>A ticket outside the caller's visibility returns {@code TICKET_NOT_FOUND} rather than
     * a forbidden error: a 403 would confirm that the id exists.</p>
     */
    private Ticket requireVisible(Caller caller, String id) {
        Ticket ticket = ticketMapper.findById(id);
        if (ticket == null) {
            throw new AuthException(TICKET_NOT_FOUND, "工单不存在");
        }
        if (!caller.canViewAll()
                && !TicketPermissions.isRequester(ticket.getRequesterId(), caller.userId())) {
            throw new AuthException(TICKET_NOT_FOUND, "工单不存在");
        }
        return ticket;
    }

    private void requireAgent(Caller caller) {
        if (!caller.isAgent()) {
            throw new AuthException(FORBIDDEN_TICKET_ACTION, "需要处理人权限");
        }
    }

    private void requireNotMerged(Ticket ticket) {
        if (ticket.isMerged()) {
            throw new AuthException(TICKET_ALREADY_MERGED, "工单已合并到 #"
                    + ticket.getMergedIntoId() + "，请在该工单上操作");
        }
    }

    private void record(
            String ticketId,
            String actorId,
            String activityType,
            String fromValue,
            String toValue,
            Map<String, Object> detail
    ) {
        String json;
        try {
            json = objectMapper.writeValueAsString(detail == null ? Map.of() : detail);
        } catch (Exception error) {
            json = "{}";
        }
        activityMapper.insert(
                UUID.randomUUID().toString(),
                ticketId,
                actorId,
                activityType,
                fromValue,
                toValue,
                json,
                LocalDateTime.now()
        );
    }

    private Map<String, Object> parseDetail(String json) {
        if (json == null || json.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(json, new TypeReference<Map<String, Object>>() {
            });
        } catch (Exception error) {
            // A malformed detail must not make the whole timeline unreadable.
            return Map.of("_unparsable", true);
        }
    }

    private Map<String, Integer> toCountMap(List<Map<String, Object>> rows) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            Object key = row.get("key");
            Object total = row.get("total");
            if (key != null && total instanceof Number number) {
                counts.put(String.valueOf(key), number.intValue());
            }
        }
        return counts;
    }

    private TicketType parseType(String raw) {
        try {
            return TicketType.parse(raw);
        } catch (IllegalArgumentException error) {
            throw new AuthException(INVALID_TICKET_TYPE, "未知工单类型：" + raw);
        }
    }

    private TicketStatus parseStatus(String raw) {
        try {
            return TicketStatus.parse(raw);
        } catch (IllegalArgumentException error) {
            throw new AuthException(INVALID_TICKET_STATUS, "未知工单状态：" + raw);
        }
    }

    private TicketLinkType parseLinkType(String raw) {
        try {
            return TicketLinkType.parse(raw);
        } catch (IllegalArgumentException error) {
            throw new AuthException(INVALID_LINK_TYPE, "未知关联类型：" + raw);
        }
    }

    private TicketPriority.Level parseLevel(String raw) {
        try {
            return TicketPriority.Level.parse(raw == null || raw.isBlank() ? "medium" : raw);
        } catch (IllegalArgumentException error) {
            throw new AuthException(INVALID_REQUEST, "未知影响/紧急度：" + raw);
        }
    }

    private String normalizePriority(String raw) {
        try {
            return TicketPriority.normalize(raw);
        } catch (IllegalArgumentException error) {
            throw new AuthException(INVALID_REQUEST, "未知优先级：" + raw);
        }
    }

    private String normalizeOptionalStatus(String raw) {
        return raw == null || raw.isBlank() ? null : parseStatus(raw).value();
    }

    private String normalizeOptionalType(String raw) {
        return raw == null || raw.isBlank() ? null : parseType(raw).value();
    }

    private String normalizeOptionalPriority(String raw) {
        return raw == null || raw.isBlank() ? null : normalizePriority(raw);
    }

    private String normalizeVisibility(String raw) {
        if (raw == null || raw.isBlank()) {
            return TicketComment.VISIBILITY_PUBLIC;
        }
        String normalized = raw.trim().toLowerCase(Locale.ROOT);
        return switch (normalized) {
            case TicketComment.VISIBILITY_PUBLIC, TicketComment.VISIBILITY_INTERNAL -> normalized;
            default -> throw new AuthException(INVALID_REQUEST, "未知可见性：" + raw);
        };
    }

    private String normalizeSource(String raw) {
        if (raw == null || raw.isBlank()) {
            return "manual";
        }
        String normalized = raw.trim().toLowerCase(Locale.ROOT);
        return switch (normalized) {
            case "manual", "chat", "email", "api" -> normalized;
            default -> "manual";
        };
    }

    private String requireText(String value, String field, int max) {
        if (value == null || value.isBlank()) {
            throw new AuthException(INVALID_REQUEST, field + " 不能为空");
        }
        String trimmed = value.trim();
        if (trimmed.length() > max) {
            throw new AuthException(INVALID_REQUEST, field + " 过长（上限 " + max + " 字符）");
        }
        return trimmed;
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }
}
