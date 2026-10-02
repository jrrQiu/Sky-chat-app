package com.skychat.config;

import com.skychat.domain.UserRoles;
import com.skychat.mapper.UserMapper;
import com.skychat.service.AuditService;
import com.skychat.service.InvitationService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.ApplicationArguments;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;

/**
 * Bootstraps the first administrator.
 *
 * <p>With self-registration off there is a chicken-and-egg problem: creating the first
 * administrator requires an administrator. This runner closes it by issuing an ordinary
 * invitation for {@code skychat.bootstrap.admin-email} when no active administrator exists.</p>
 *
 * <p>It deliberately does not create the account directly: an invite link is one-time, hashed
 * at rest, expires, and lets the operator choose their own password, so no credential ever
 * passes through configuration or a log file. The link is printed once at WARN level and the
 * invitation is recorded in {@code audit_log} like any other.</p>
 */
@Component
public class BootstrapAdminInitializer implements ApplicationRunner {
    private static final Logger log = LoggerFactory.getLogger(BootstrapAdminInitializer.class);

    private final SecurityProperties properties;
    private final UserMapper userMapper;
    private final InvitationService invitationService;
    private final AuditService auditService;

    public BootstrapAdminInitializer(
            SecurityProperties properties,
            UserMapper userMapper,
            InvitationService invitationService,
            AuditService auditService
    ) {
        this.properties = properties;
        this.userMapper = userMapper;
        this.invitationService = invitationService;
        this.auditService = auditService;
    }

    @Override
    public void run(ApplicationArguments args) {
        String adminEmail = properties == null || properties.bootstrap() == null
                ? null
                : properties.bootstrap().adminEmail();
        if (adminEmail == null || adminEmail.isBlank()) {
            return;
        }

        try {
            if (userMapper.countAdministrators() > 0) {
                log.info("Bootstrap admin skipped: an active administrator already exists.");
                return;
            }
            InvitationService.IssuedInvitation issued = invitationService.create(
                    adminEmail.trim(),
                    "管理员",
                    List.of(UserRoles.ADMIN),
                    null,
                    "system"
            );
            String inviteUrl = invitationService.inviteUrl(issued.rawToken());
            auditService.record(
                    AuditService.Context.system(),
                    "user.bootstrap_admin",
                    "invitation",
                    issued.invitation().getId(),
                    AuditService.OUTCOME_SUCCESS,
                    Map.of("email", issued.invitation().getEmail())
            );
            log.warn("""

                    ============================================================
                    未检测到管理员账号，已为 {} 生成管理员邀请链接（仅显示这一次）：
                    {}
                    请通过该链接设置密码完成初始化。
                    ============================================================
                    """, issued.invitation().getEmail(), inviteUrl);
        } catch (RuntimeException error) {
            // A failed bootstrap must not stop the service: an operator can still fix the
            // configuration and restart, and refusing to boot would turn a setup mistake
            // into an outage.
            log.error("Bootstrap admin invitation could not be created: {}", error.getMessage());
        }
    }
}
