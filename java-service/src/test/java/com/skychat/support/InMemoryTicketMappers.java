package com.skychat.support;

import com.skychat.domain.ticket.Ticket;
import com.skychat.domain.ticket.TicketComment;
import com.skychat.domain.ticket.TicketLink;
import com.skychat.mapper.TicketActivityMapper;
import com.skychat.mapper.TicketActivityRow;
import com.skychat.mapper.TicketCommentMapper;
import com.skychat.mapper.TicketLinkMapper;
import com.skychat.mapper.TicketMapper;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * In-memory ticket mappers whose behaviour mirrors the SQL they replace.
 *
 * <p>Hand-written rather than mocked: Mockito's inline mock maker cannot attach on this JDK, and
 * the properties worth testing here — the visibility predicate, the single-winner merge, the
 * recursive merge chain, the internal-comment filter — are exactly the ones a stub would let a
 * test pass while the real SQL did something else.</p>
 */
public final class InMemoryTicketMappers {

    private InMemoryTicketMappers() {
    }

    /** Shared state so the four mappers see one dataset. */
    public static final class Store {
        public final List<Ticket> tickets = new ArrayList<>();
        public final List<TicketComment> comments = new ArrayList<>();
        public final List<TicketActivityRow> activities = new ArrayList<>();
        public final List<TicketLink> links = new ArrayList<>();
        private final AtomicLong numbers = new AtomicLong(1000);

        public long nextNumber() {
            return numbers.incrementAndGet();
        }

        public Ticket ticket(String id) {
            return tickets.stream().filter(t -> t.getId().equals(id)).findFirst().orElse(null);
        }

        /**
         * A detached copy, mirroring the SQL: every read of a row produces a fresh object.
         *
         * <p>Returning the stored instance would let the service accidentally depend on the
         * write mutating the object it just read — which is how an earlier version of the
         * update path silently stopped recording timeline entries in these tests while working
         * in production.</p>
         */
        public Ticket copyOf(Ticket source) {
            if (source == null) {
                return null;
            }
            Ticket copy = new Ticket();
            copy.setId(source.getId());
            copy.setNumber(source.getNumber());
            copy.setTenantId(source.getTenantId());
            copy.setType(source.getType());
            copy.setStatus(source.getStatus());
            copy.setPriority(source.getPriority());
            copy.setImpact(source.getImpact());
            copy.setUrgency(source.getUrgency());
            copy.setTitle(source.getTitle());
            copy.setDescription(source.getDescription());
            copy.setRequesterId(source.getRequesterId());
            copy.setAssigneeId(source.getAssigneeId());
            copy.setParentId(source.getParentId());
            copy.setSource(source.getSource());
            copy.setConversationId(source.getConversationId());
            copy.setMergedIntoId(source.getMergedIntoId());
            copy.setResolution(source.getResolution());
            copy.setCreatedBy(source.getCreatedBy());
            copy.setFirstResponseAt(source.getFirstResponseAt());
            copy.setResolvedAt(source.getResolvedAt());
            copy.setClosedAt(source.getClosedAt());
            copy.setCreatedAt(source.getCreatedAt());
            copy.setUpdatedAt(source.getUpdatedAt());
            return copy;
        }
    }

    public static final class TicketMapperFake implements TicketMapper {
        private final Store store;

        public TicketMapperFake(Store store) {
            this.store = store;
        }

        @Override
        public long nextNumber() {
            return store.nextNumber();
        }

        @Override
        public int insert(Ticket ticket) {
            store.tickets.add(ticket);
            return 1;
        }

        @Override
        public Ticket findById(String id) {
            return store.copyOf(store.ticket(id));
        }

        @Override
        public Ticket findByNumber(long number) {
            return store.copyOf(store.tickets.stream()
                    .filter(t -> t.getNumber() == number)
                    .findFirst()
                    .orElse(null));
        }

        @Override
        public List<Ticket> search(
                String query, String status, String type, String priority, String assigneeId,
                String requesterId, String visibleToRequester, boolean includeMerged,
                int limit, int offset
        ) {
            List<Ticket> matched = filter(
                    query, status, type, priority, assigneeId, requesterId, visibleToRequester,
                    includeMerged
            );
            if (offset >= matched.size()) {
                return List.of();
            }
            // Detached copies again: a search result must not alias the stored row.
            return matched.subList(offset, Math.min(offset + limit, matched.size())).stream()
                    .map(store::copyOf)
                    .toList();
        }

        @Override
        public int countSearch(
                String query, String status, String type, String priority, String assigneeId,
                String requesterId, String visibleToRequester, boolean includeMerged
        ) {
            return filter(query, status, type, priority, assigneeId, requesterId,
                    visibleToRequester, includeMerged).size();
        }

        private List<Ticket> filter(
                String query, String status, String type, String priority, String assigneeId,
                String requesterId, String visibleToRequester, boolean includeMerged
        ) {
            return store.tickets.stream()
                    // The security predicate: a non-agent is pinned to their own tickets.
                    .filter(t -> visibleToRequester == null
                            || visibleToRequester.equals(t.getRequesterId()))
                    .filter(t -> status == null || status.equals(t.getStatus()))
                    .filter(t -> type == null || type.equals(t.getType()))
                    .filter(t -> priority == null || priority.equals(t.getPriority()))
                    .filter(t -> assigneeId == null || assigneeId.equals(t.getAssigneeId()))
                    .filter(t -> requesterId == null || requesterId.equals(t.getRequesterId()))
                    .filter(t -> includeMerged || t.getMergedIntoId() == null)
                    .filter(t -> query == null || query.isBlank()
                            || t.getTitle().toLowerCase(Locale.ROOT)
                                    .contains(query.toLowerCase(Locale.ROOT))
                            || t.getDescription().toLowerCase(Locale.ROOT)
                                    .contains(query.toLowerCase(Locale.ROOT))
                            || String.valueOf(t.getNumber()).equals(query))
                    .sorted(Comparator.comparing(Ticket::getUpdatedAt,
                                    Comparator.nullsLast(Comparator.reverseOrder()))
                            .thenComparing(Ticket::getNumber, Comparator.reverseOrder()))
                    .toList();
        }

        @Override
        public List<Map<String, Object>> countByStatus(String visibleToRequester) {
            return counts(visibleToRequester, Ticket::getStatus);
        }

        @Override
        public List<Map<String, Object>> countByPriority(String visibleToRequester) {
            return counts(visibleToRequester, Ticket::getPriority);
        }

        private List<Map<String, Object>> counts(
                String visibleToRequester,
                java.util.function.Function<Ticket, String> key
        ) {
            Map<String, Integer> grouped = new LinkedHashMap<>();
            for (Ticket ticket : filter(null, null, null, null, null, null,
                    visibleToRequester, false)) {
                grouped.merge(key.apply(ticket), 1, Integer::sum);
            }
            List<Map<String, Object>> rows = new ArrayList<>();
            grouped.forEach((k, v) -> rows.add(Map.of("key", k, "total", v)));
            return rows;
        }

        @Override
        public List<Ticket> findChildren(String parentId) {
            return store.tickets.stream()
                    .filter(t -> parentId.equals(t.getParentId()))
                    .sorted(Comparator.comparing(Ticket::getCreatedAt))
                    .map(store::copyOf)
                    .toList();
        }

        @Override
        public int updateFields(
                String id, String title, String description, String type, String impact,
                String urgency, String priority, String assigneeId, LocalDateTime updatedAt
        ) {
            Ticket ticket = store.ticket(id);
            if (ticket == null) {
                return 0;
            }
            if (title != null) {
                ticket.setTitle(title);
            }
            if (description != null) {
                ticket.setDescription(description);
            }
            if (type != null) {
                ticket.setType(type);
            }
            if (impact != null) {
                ticket.setImpact(impact);
            }
            if (urgency != null) {
                ticket.setUrgency(urgency);
            }
            if (priority != null) {
                ticket.setPriority(priority);
            }
            if (assigneeId != null) {
                ticket.setAssigneeId(assigneeId.isBlank() ? null : assigneeId);
            }
            ticket.setUpdatedAt(updatedAt);
            return 1;
        }

        @Override
        public int updateStatus(
                String id, String status, String resolution, LocalDateTime firstResponseAt,
                LocalDateTime resolvedAt, LocalDateTime closedAt, LocalDateTime updatedAt
        ) {
            Ticket ticket = store.ticket(id);
            if (ticket == null) {
                return 0;
            }
            ticket.setStatus(status);
            ticket.setResolution(resolution);
            // Mirrors `COALESCE(first_response_at, #{firstResponseAt})`: the first response is
            // historical and must not be cleared by later transitions.
            if (ticket.getFirstResponseAt() == null && firstResponseAt != null) {
                ticket.setFirstResponseAt(firstResponseAt);
            }
            ticket.setResolvedAt(resolvedAt);
            ticket.setClosedAt(closedAt);
            ticket.setUpdatedAt(updatedAt);
            return 1;
        }

        @Override
        public int markFirstResponse(String id, LocalDateTime at) {
            Ticket ticket = store.ticket(id);
            if (ticket == null || ticket.getFirstResponseAt() != null) {
                return 0;
            }
            ticket.setFirstResponseAt(at);
            return 1;
        }

        @Override
        public int touch(String id, LocalDateTime updatedAt) {
            Ticket ticket = store.ticket(id);
            if (ticket == null) {
                return 0;
            }
            ticket.setUpdatedAt(updatedAt);
            return 1;
        }

        /** Single-winner: only the first merge of a ticket succeeds. */
        @Override
        public int markMerged(String id, String mergedIntoId, LocalDateTime closedAt) {
            Ticket ticket = store.ticket(id);
            if (ticket == null || ticket.getMergedIntoId() != null) {
                return 0;
            }
            ticket.setMergedIntoId(mergedIntoId);
            ticket.setStatus("closed");
            if (ticket.getClosedAt() == null) {
                ticket.setClosedAt(closedAt);
            }
            ticket.setUpdatedAt(closedAt);
            return 1;
        }
    }

    public static final class CommentMapperFake implements TicketCommentMapper {
        private final Store store;

        public CommentMapperFake(Store store) {
            this.store = store;
        }

        @Override
        public int insert(TicketComment comment) {
            store.comments.add(comment);
            return 1;
        }

        @Override
        public TicketComment findById(String id) {
            return store.comments.stream()
                    .filter(c -> c.getId().equals(id))
                    .findFirst()
                    .orElse(null);
        }

        @Override
        public List<TicketComment> findByTicket(String ticketId, boolean includeInternal) {
            return store.comments.stream()
                    .filter(c -> ticketId.equals(c.getTicketId()))
                    // The internal filter lives in SQL so no caller can forget it.
                    .filter(c -> includeInternal || !c.isInternal())
                    .sorted(Comparator.comparing(TicketComment::getCreatedAt)
                            .thenComparing(TicketComment::getId))
                    .toList();
        }

        @Override
        public int countByTicket(String ticketId) {
            return (int) store.comments.stream()
                    .filter(c -> ticketId.equals(c.getTicketId()))
                    .count();
        }

        @Override
        public int moveToTicket(String sourceId, String targetId) {
            int moved = 0;
            for (TicketComment comment : store.comments) {
                if (sourceId.equals(comment.getTicketId())) {
                    comment.setTicketId(targetId);
                    moved++;
                }
            }
            return moved;
        }
    }

    public static final class ActivityMapperFake implements TicketActivityMapper {
        private final Store store;

        public ActivityMapperFake(Store store) {
            this.store = store;
        }

        @Override
        public int insert(
                String id, String ticketId, String actorId, String activityType, String fromValue,
                String toValue, String detailJson, LocalDateTime createdAt
        ) {
            store.activities.add(new TicketActivityRow(
                    id, ticketId, actorId, activityType, fromValue, toValue, detailJson, createdAt
            ));
            return 1;
        }

        @Override
        public List<TicketActivityRow> findByTicket(String ticketId, int limit) {
            List<TicketActivityRow> matched = store.activities.stream()
                    .filter(a -> ticketId.equals(a.ticketId()))
                    .sorted(Comparator.comparing(TicketActivityRow::createdAt)
                            .thenComparing(TicketActivityRow::id))
                    .toList();
            return matched.size() > limit ? matched.subList(0, limit) : matched;
        }

        @Override
        public int moveToTicket(String sourceId, String targetId) {
            int moved = 0;
            for (int i = 0; i < store.activities.size(); i++) {
                TicketActivityRow row = store.activities.get(i);
                if (sourceId.equals(row.ticketId())) {
                    store.activities.set(i, new TicketActivityRow(
                            row.id(), targetId, row.actorId(), row.activityType(),
                            row.fromValue(), row.toValue(), row.detailJson(), row.createdAt()
                    ));
                    moved++;
                }
            }
            return moved;
        }
    }

    public static final class LinkMapperFake implements TicketLinkMapper {
        private final Store store;

        public LinkMapperFake(Store store) {
            this.store = store;
        }

        @Override
        public int insert(TicketLink link) {
            store.links.add(link);
            return 1;
        }

        @Override
        public TicketLink findById(String id) {
            return store.links.stream()
                    .filter(l -> l.getId().equals(id))
                    .findFirst()
                    .orElse(null);
        }

        @Override
        public List<TicketLink> findOutgoing(String ticketId) {
            return store.links.stream()
                    .filter(l -> ticketId.equals(l.getTicketId()))
                    .sorted(Comparator.comparing(TicketLink::getCreatedAt))
                    .toList();
        }

        @Override
        public List<TicketLink> findIncoming(String ticketId) {
            return store.links.stream()
                    .filter(l -> ticketId.equals(l.getLinkedTicketId()))
                    .sorted(Comparator.comparing(TicketLink::getCreatedAt))
                    .toList();
        }

        @Override
        public int exists(String ticketId, String linkedTicketId, String linkType) {
            return (int) store.links.stream()
                    .filter(l -> ticketId.equals(l.getTicketId())
                            && linkedTicketId.equals(l.getLinkedTicketId())
                            && linkType.equals(l.getLinkType()))
                    .count();
        }

        @Override
        public int delete(String id, String ticketId) {
            boolean removed = store.links.removeIf(
                    l -> l.getId().equals(id) && l.getTicketId().equals(ticketId)
            );
            return removed ? 1 : 0;
        }

        /** Walks the merge chain from {@code ticketId} and reports whether it reaches the target. */
        @Override
        public int mergeChainContains(String ticketId, String candidateId) {
            String current = ticketId;
            int guard = 0;
            while (current != null && guard++ < 50) {
                if (current.equals(candidateId)) {
                    return 1;
                }
                Ticket ticket = store.ticket(current);
                current = ticket == null ? null : ticket.getMergedIntoId();
            }
            return 0;
        }

        @Override
        public int repointIncoming(String sourceId, String targetId) {
            int moved = 0;
            for (TicketLink link : store.links) {
                if (sourceId.equals(link.getLinkedTicketId())) {
                    link.setLinkedTicketId(targetId);
                    moved++;
                }
            }
            return moved;
        }

        @Override
        public int repointOutgoing(String sourceId, String targetId) {
            int moved = 0;
            for (TicketLink link : store.links) {
                if (sourceId.equals(link.getTicketId())) {
                    link.setTicketId(targetId);
                    moved++;
                }
            }
            return moved;
        }
    }
}
