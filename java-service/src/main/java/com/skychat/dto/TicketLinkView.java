package com.skychat.dto;

import java.time.LocalDateTime;

/**
 * A relation as seen from one ticket.
 *
 * <p>{@code direction} distinguishes a link this ticket declared ({@code outgoing}) from one
 * declared by the other side ({@code incoming}). The display fields are joined in so the UI can
 * render "#1042 打印机离线 (处理中)" without a second round trip per link.</p>
 */
public record TicketLinkView(
        String id,
        String ticketId,
        String linkedTicketId,
        String linkType,
        String direction,
        Long linkedNumber,
        String linkedTitle,
        String linkedStatus,
        String createdBy,
        LocalDateTime createdAt
) {
    public static final String OUTGOING = "outgoing";
    public static final String INCOMING = "incoming";
}
