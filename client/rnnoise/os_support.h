/* stand-in for the os_support.h missing from the rnnoise-0.2 tarball */
#ifndef OS_SUPPORT_H
#define OS_SUPPORT_H
#include <string.h>
#include <stdlib.h>
#ifndef OPUS_INLINE
#define OPUS_INLINE inline
#endif
#define OPUS_COPY(dst, src, n) (memcpy((dst), (src), (n)*sizeof(*(dst)) + 0*((dst)-(src))))
#define OPUS_MOVE(dst, src, n) (memmove((dst), (src), (n)*sizeof(*(dst)) + 0*((dst)-(src))))
#define OPUS_CLEAR(dst, n) (memset((dst), 0, (n)*sizeof(*(dst))))
#endif
