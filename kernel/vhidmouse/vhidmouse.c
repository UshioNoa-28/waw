/*
 * vhidmouse.c - KMDF virtual HID mouse built on the Windows Virtual HID
 * Framework (VHF).
 *
 * Why VHF: input authored here is delivered through hidclass.sys ->
 * mouclass.sys -> win32k.sys with a real IRP and kernel timestamp, so the
 * input stack treats it as hardware.  Nothing hooks system structures and
 * nothing patches the running kernel image; KPP/PatchGuard stay happy.
 *
 * Transport: a lock-free SPSC ring in a named section, mapped into the
 * client process.  A passive-level timer drains the ring and calls
 * VhfReadReportSubmit().  IOCTLs are lifecycle only.
 */

#include <initguid.h>
#include <ntddk.h>
#include <wdf.h>
#include <vhf.h>
#include "..\shared\vhid_proto.h"

#define VHID_TAG 'DHiV'

/* Timer drain interval, in ms.  Well under a 1000 Hz mouse poll. */
#define VHID_DRAIN_INTERVAL_MS 1

/* Reports forwarded per drain tick, to bound DPC time. */
#define VHID_DRAIN_BATCH 8

typedef struct _VHID_CONTEXT
{
    VHFHANDLE VhfHandle;
    HANDLE SectionHandle;
    PVOID SectionBase;
    PVOID Ring;
    WDFTIMER DrainTimer;
    LONG Attached;
} VHID_CONTEXT, *PVHID_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(VHID_CONTEXT, VhidGetContext)

static VOID VhidEvtCleanup(_In_ WDFOBJECT Object);

/* ------------------------------------------------------------------ */
/* HID report descriptor: 5 buttons, relative X/Y, wheel.              */
/* ------------------------------------------------------------------ */

static const UCHAR g_ReportDescriptor[] = {
    0x05, 0x01,        /* Usage Page (Generic Desktop) */
    0x09, 0x02,        /* Usage (Mouse) */
    0xA1, 0x01,        /* Collection (Application) */
    0x09, 0x01,        /*   Usage (Pointer) */
    0xA1, 0x00,        /*   Collection (Physical) */
    0x05, 0x09,        /*     Usage Page (Button) */
    0x19, 0x01,        /*     Usage Minimum (1) */
    0x29, 0x05,        /*     Usage Maximum (5) */
    0x15, 0x00,        /*     Logical Minimum (0) */
    0x25, 0x01,        /*     Logical Maximum (1) */
    0x95, 0x05,        /*     Report Count (5) */
    0x75, 0x01,        /*     Report Size (1) */
    0x81, 0x02,        /*     Input (Data,Var,Abs) */
    0x95, 0x03,        /*     Report Count (3) padding */
    0x75, 0x01,        /*     Report Size (1) */
    0x81, 0x03,        /*     Input (Cnst,Var,Abs) */
    0x05, 0x01,        /*     Usage Page (Generic Desktop) */
    0x09, 0x30,        /*     Usage (X) */
    0x09, 0x31,        /*     Usage (Y) */
    0x15, 0x81,        /*     Logical Minimum (-127) */
    0x25, 0x7F,        /*     Logical Maximum (127) */
    0x75, 0x08,        /*     Report Size (8) */
    0x95, 0x02,        /*     Report Count (2) */
    0x81, 0x06,        /*     Input (Data,Var,Rel) */
    0x09, 0x38,        /*     Usage (Wheel) */
    0x15, 0x81,        /*     Logical Minimum (-127) */
    0x25, 0x7F,        /*     Logical Maximum (127) */
    0x75, 0x08,        /*     Report Size (8) */
    0x95, 0x01,        /*     Report Count (1) */
    0x81, 0x06,        /*     Input (Data,Var,Rel) */
    0xC0,              /*   End Collection */
    0xC0,              /* End Collection */
};

/* ------------------------------------------------------------------ */
/* Shared ring lifecycle                                               */
/* ------------------------------------------------------------------ */

static NTSTATUS
VhidCreateSection(_Inout_ PVHID_CONTEXT ctx)
{
    NTSTATUS status;
    OBJECT_ATTRIBUTES oa;
    UNICODE_STRING name;
    LARGE_INTEGER size;

    RtlInitUnicodeString(&name, VHID_SECTION_NAME);
    InitializeObjectAttributes(&oa, &name,
        OBJ_CASE_INSENSITIVE | OBJ_KERNEL_HANDLE, NULL, NULL);
    size.QuadPart = VHID_SECTION_SIZE;

    status = ZwCreateSection(&ctx->SectionHandle, SECTION_ALL_ACCESS, &oa, &size,
        PAGE_READWRITE, SEC_COMMIT, NULL);
    if (!NT_SUCCESS(status))
    {
        DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_ERROR_LEVEL,
            "[vhid] ZwCreateSection 0x%x\n", status);
        return status;
    }

    status = ZwMapViewOfSection(ctx->SectionHandle, NtCurrentProcess(),
        &ctx->SectionBase, 0, 0, NULL, &size, ViewUnmap, 0, PAGE_READWRITE);
    if (!NT_SUCCESS(status))
    {
        DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_ERROR_LEVEL,
            "[vhid] ZwMapViewOfSection 0x%x\n", status);
        ZwClose(ctx->SectionHandle);
        ctx->SectionHandle = NULL;
        return status;
    }

    ctx->Ring = ctx->SectionBase;
    RtlZeroMemory(ctx->Ring, VHID_SECTION_SIZE);
    return STATUS_SUCCESS;
}

static VOID
VhidDestroySection(_Inout_ PVHID_CONTEXT ctx)
{
    if (ctx->Ring != NULL)
    {
        ((PVHID_RING)ctx->Ring)->Header.Attached = 0;
    }
    if (ctx->SectionBase != NULL)
    {
        ZwUnmapViewOfSection(NtCurrentProcess(), ctx->SectionBase);
        ctx->SectionBase = NULL;
        ctx->Ring = NULL;
    }
    if (ctx->SectionHandle != NULL)
    {
        ZwClose(ctx->SectionHandle);
        ctx->SectionHandle = NULL;
    }
}

/* ------------------------------------------------------------------ */
/* Drain timer                                                         */
/* ------------------------------------------------------------------ */

static VOID
VhidDrainTimer(_In_ WDFTIMER Timer)
{
    WDFDEVICE device = WdfTimerGetParentObject(Timer);
    PVHID_CONTEXT ctx = VhidGetContext(device);
    PVHID_RING ring = (PVHID_RING)ctx->Ring;
    int budget = VHID_DRAIN_BATCH;

    if (ring == NULL || ctx->VhfHandle == NULL || ctx->Attached == 0)
    {
        return;
    }

    while (budget-- > 0)
    {
        HID_XFER_PACKET packet;
        NTSTATUS submitStatus;
        LONG head = ring->Header.Head;
        LONG tail = ring->Header.Tail;
        VHID_REPORT report;

        if (tail == head)
        {
            break;
        }

        report = ring->Slots[tail & VHID_RING_MASK];
        InterlockedExchange(&ring->Header.Tail, tail + 1);

        packet.reportBuffer = (PUCHAR)&report;
        packet.reportBufferLen = sizeof(report);
        packet.reportId = 0;

        submitStatus = VhfReadReportSubmit(ctx->VhfHandle, &packet);
        if (!NT_SUCCESS(submitStatus))
        {
            DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_WARNING_LEVEL,
                "[vhid] VhfReadReportSubmit 0x%x\n", submitStatus);
        }
    }
}

/* ------------------------------------------------------------------ */
/* IOCTL handling                                                      */
/* ------------------------------------------------------------------ */

static VOID
VhidIoControl(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t OutputBufferLength,
    _In_ size_t InputBufferLength,
    _In_ ULONG IoControlCode
    )
{
    WDFDEVICE device = WdfIoQueueGetDevice(Queue);
    PVHID_CONTEXT ctx = VhidGetContext(device);
    NTSTATUS status = STATUS_INVALID_DEVICE_REQUEST;
    size_t written = 0;

    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    switch (IoControlCode)
    {
    case IOCTL_VHID_ATTACH:
        if (ctx->VhfHandle == NULL)
        {
            VHF_CONFIG config;
            VHF_CONFIG_INIT(&config, WdfDeviceWdmGetDeviceObject(device),
                sizeof(g_ReportDescriptor), (PUCHAR)g_ReportDescriptor);
            config.VhfClientContext = ctx;
            status = VhfCreate(&config, &ctx->VhfHandle);
            if (!NT_SUCCESS(status))
            {
                ctx->VhfHandle = NULL;
                break;
            }
        }
        ctx->Attached = 1;
        if (ctx->Ring != NULL)
        {
            ((PVHID_RING)ctx->Ring)->Header.Attached = 1;
        }
        status = STATUS_SUCCESS;
        DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_INFO_LEVEL, "[vhid] attached\n");
        break;

    case IOCTL_VHID_DETACH:
        ctx->Attached = 0;
        if (ctx->Ring != NULL)
        {
            ((PVHID_RING)ctx->Ring)->Header.Attached = 0;
        }
        if (ctx->VhfHandle != NULL)
        {
            VhfDelete(ctx->VhfHandle, TRUE);
            ctx->VhfHandle = NULL;
        }
        status = STATUS_SUCCESS;
        DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_INFO_LEVEL, "[vhid] detached\n");
        break;

    case IOCTL_VHID_QUERY:
    {
        PVHID_QUERY_REPLY reply = NULL;
        status = WdfRequestRetrieveOutputBuffer(Request,
            sizeof(VHID_QUERY_REPLY), (PVOID*)&reply, NULL);
        if (NT_SUCCESS(status))
        {
            LONG head = 0, tail = 0;
            if (ctx->Ring != NULL)
            {
                head = ((PVHID_RING)ctx->Ring)->Header.Head;
                tail = ((PVHID_RING)ctx->Ring)->Header.Tail;
            }
            reply->ProtocolVersion = VHID_PROTO_VERSION;
            reply->Attached = (ULONG)(ctx->Attached != 0);
            reply->Dropped = (ctx->Ring != NULL)
                ? ((PVHID_RING)ctx->Ring)->Header.Dropped : 0;
            reply->Pending = head - tail;
            written = sizeof(VHID_QUERY_REPLY);
        }
        break;
    }

    default:
        break;
    }

    WdfRequestCompleteWithInformation(Request, status, written);
}

/* ------------------------------------------------------------------ */
/* Driver plumbing                                                     */
/* ------------------------------------------------------------------ */

static NTSTATUS
VhidEvtDeviceAdd(_In_ WDFDRIVER Driver, _Inout_ PWDFDEVICE_INIT DeviceInit)
{
    NTSTATUS status;
    WDFDEVICE device;
    WDF_OBJECT_ATTRIBUTES attribs;
    WDF_IO_QUEUE_CONFIG queueConfig;
    WDF_TIMER_CONFIG timerConfig;
    UNICODE_STRING devName;
    UNICODE_STRING symlinkName;
    PVHID_CONTEXT ctx;

    UNREFERENCED_PARAMETER(Driver);

    WdfDeviceInitSetDeviceType(DeviceInit, FILE_DEVICE_UNKNOWN);
    WdfDeviceInitSetExclusive(DeviceInit, FALSE);
    WdfDeviceInitSetIoType(DeviceInit, WdfDeviceIoBuffered);

    RtlInitUnicodeString(&devName, VHID_DEVICE_NAME);
    status = WdfDeviceInitAssignName(DeviceInit, &devName);
    if (!NT_SUCCESS(status))
    {
        return status;
    }

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attribs, VHID_CONTEXT);
    attribs.EvtCleanupCallback = VhidEvtCleanup;
    status = WdfDeviceCreate(&DeviceInit, &attribs, &device);
    if (!NT_SUCCESS(status))
    {
        return status;
    }

    RtlInitUnicodeString(&symlinkName, VHID_SYMLINK_NAME);
    status = WdfDeviceCreateSymbolicLink(device, &symlinkName);
    if (!NT_SUCCESS(status))
    {
        DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_ERROR_LEVEL,
            "[vhid] symbolic link 0x%x\n", status);
        return status;
    }

    ctx = VhidGetContext(device);
    RtlZeroMemory(ctx, sizeof(*ctx));
    ctx->Attached = 0;

    status = VhidCreateSection(ctx);
    if (!NT_SUCCESS(status))
    {
        return status;
    }

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&queueConfig,
        WdfIoQueueDispatchParallel);
    queueConfig.EvtIoDeviceControl = VhidIoControl;
    status = WdfIoQueueCreate(device, &queueConfig,
        WDF_NO_OBJECT_ATTRIBUTES, NULL);
    if (!NT_SUCCESS(status))
    {
        return status;
    }

    WDF_TIMER_CONFIG_INIT_PERIODIC(&timerConfig, VhidDrainTimer,
        VHID_DRAIN_INTERVAL_MS);
    WDF_OBJECT_ATTRIBUTES_INIT(&attribs);
    attribs.ParentObject = device;
    attribs.ExecutionLevel = WdfExecutionLevelPassive;
    status = WdfTimerCreate(&timerConfig, &attribs, &ctx->DrainTimer);
    if (!NT_SUCCESS(status))
    {
        return status;
    }

    WdfTimerStart(ctx->DrainTimer, WDF_REL_TIMEOUT_IN_MS(VHID_DRAIN_INTERVAL_MS));

    DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_INFO_LEVEL,
        "[vhid] device ready, ring mapped at %p\n", ctx->Ring);
    return STATUS_SUCCESS;
}

static VOID
VhidEvtCleanup(_In_ WDFOBJECT Object)
{
    WDFDEVICE device = (WDFDEVICE)Object;
    PVHID_CONTEXT ctx = VhidGetContext(device);

    if (ctx->DrainTimer != NULL)
    {
        WdfTimerStop(ctx->DrainTimer, TRUE);
    }
    if (ctx->VhfHandle != NULL)
    {
        VhfDelete(ctx->VhfHandle, TRUE);
        ctx->VhfHandle = NULL;
    }
    VhidDestroySection(ctx);
}

NTSTATUS
DriverEntry(_In_ PDRIVER_OBJECT DriverObject, _In_ PUNICODE_STRING RegistryPath)
{
    WDF_DRIVER_CONFIG config;
    NTSTATUS status;

    WDF_DRIVER_CONFIG_INIT(&config, VhidEvtDeviceAdd);
    status = WdfDriverCreate(DriverObject, RegistryPath,
        WDF_NO_OBJECT_ATTRIBUTES, &config, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status))
    {
        DbgPrintEx(DPFLTR_IHVDRIVER_ID, DPFLTR_ERROR_LEVEL,
            "[vhid] WdfDriverCreate 0x%x\n", status);
    }
    return status;
}
