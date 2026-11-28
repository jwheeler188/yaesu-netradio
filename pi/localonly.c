/* yaesu-netradio: keeps a program's listening sockets on this computer only.
 *
 * Dire Wolf 1.7 always listens for AGW clients on every network ("any address"), and anyone who can reach that port can
 * transmit. For Winlink over packet the bridge starts Dire Wolf with this library preloaded (LD_PRELOAD): every bind() to
 * "any address" becomes a bind to 127.0.0.1 or ::1, so only programs on the Pi (Pat) can use it. Other binds are untouched.
 * The bridge also checks /proc/net/tcp afterwards and stops Dire Wolf if the port is open to the network anyway.
 *
 * Built by the bridge the first time it is needed:  cc -shared -fPIC -O2 -o localonly.so localonly.c -ldl
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <netinet/in.h>
#include <string.h>
#include <sys/socket.h>

int bind(int fd, const struct sockaddr *sa, socklen_t len) {
    static int (*real_bind)(int, const struct sockaddr *, socklen_t);
    if (!real_bind) real_bind = (int (*)(int, const struct sockaddr *, socklen_t))dlsym(RTLD_NEXT, "bind");
    if (sa && sa->sa_family == AF_INET && len >= (socklen_t)sizeof(struct sockaddr_in)) {
        struct sockaddr_in a; memcpy(&a, sa, sizeof a);
        if (a.sin_addr.s_addr == htonl(INADDR_ANY)) { a.sin_addr.s_addr = htonl(INADDR_LOOPBACK); return real_bind(fd, (struct sockaddr *)&a, sizeof a); }
    }
    if (sa && sa->sa_family == AF_INET6 && len >= (socklen_t)sizeof(struct sockaddr_in6)) {
        struct sockaddr_in6 a; memcpy(&a, sa, sizeof a);
        if (!memcmp(&a.sin6_addr, &in6addr_any, sizeof a.sin6_addr)) { a.sin6_addr = in6addr_loopback; return real_bind(fd, (struct sockaddr *)&a, sizeof a); }
    }
    return real_bind(fd, sa, len);
}
