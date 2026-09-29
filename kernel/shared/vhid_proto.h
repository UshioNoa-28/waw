/*
 * vhid_proto.h - shared contract between the user-mode client and the
 * VHF virtual mouse driver.
 *
 * The driver exposes a named section (shared memory) and a control device.
 * The user-mode side writes command packets into a lock-free ring buffer;
 * the driver drains them from a passive-level DPC and forwards each packet
 * to the Virtual HID Framework as an input report, which then travels the
 * normal HID stack (hidclass -> mouclass -> win32k) exactly like a physical
 * mouse.
 */

#ifndef VHID_PROTO_H
#define VHID_PROTO_H

#ifdef _KERNEL_MODE
#include <ntddk.h>
#else
#include <windows.h>
#endif

#define VHID_PROTO_VERSION 1

/*
 * Named objects.  Keep these innocuous and stable; the user-mode client and
 * the driver must agree on them.
 */
#define VHID_SECTION_NAME   L"\\BaseNamedObjects\\Global\\VhidReportRing"
#define VHID_DEVICE_NAME    L"\\Device\\VhidMouse"
#define VHID_SYMLINK_NAME   L"\\DosDevices\\Global\\VhidMouse"

/*
 * IOCTL codes.  The ring buffer is the hot path; the IOCTLs are only for
 * lifecycle (attach/detach/query).
 *
 *   CTL_CODE(FileDevice 0x8000 + our function range, ...)
 */
#define VHID_DEVICE_TYPE 0x8000

#define IOCTL_VHID_ATTACH \
    CTL_CODE(VHID_DEVICE_TYPE, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_VHID_DETACH \
    CTL_CODE(VHID_DEVICE_TYPE, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_VHID_QUERY \
    CTL_CODE(VHID_DEVICE_TYPE, 0x802, METHOD_BUFFERED, FILE_ANY_ACCESS)

/* Ring depth.  Must be a power of two. */
#define VHID_RING_SLOTS 256
#define VHID_RING_MASK (VHID_RING_SLOTS - 1)

/* HID report: 5 buttons, relative X/Y, wheel.  Matches the report descriptor. */
#pragma pack(push, 1)
typedef struct _VHID_REPORT
{
    UCHAR Buttons;  /* bit0 left, bit1 right, bit2 middle, bit3 b4, bit4 b5 */
    CHAR  Dx;       /* relative X, -127..127 */
    CHAR  Dy;       /* relative Y, -127..127 */
    CHAR  Wheel;    /* relative vertical wheel */
} VHID_REPORT, *PVHID_REPORT;
#pragma pack(pop)

typedef struct _VHID_RING_HEADER
{
    volatile LONG Head;      /* producer (user-mode) writes here */
    volatile LONG Tail;      /* consumer (driver) writes here */
    volatile LONG Attached;  /* 1 while the driver owns the VHF device */
    volatile LONG Dropped;   /* packets dropped because the ring was full */
    volatile LONG Pad;
} VHID_RING_HEADER, *PVHID_RING_HEADER;

typedef struct _VHID_RING
{
    VHID_RING_HEADER Header;
    VHID_REPORT Slots[VHID_RING_SLOTS];
} VHID_RING, *PVHID_RING;

#define VHID_SECTION_SIZE ((ULONG)sizeof(VHID_RING))

typedef struct _VHID_QUERY_REPLY
{
    ULONG ProtocolVersion;
    ULONG Attached;
    LONG  Dropped;
    LONG  Pending; /* slots currently queued */
} VHID_QUERY_REPLY, *PVHID_QUERY_REPLY;

#endif /* VHID_PROTO_H */
