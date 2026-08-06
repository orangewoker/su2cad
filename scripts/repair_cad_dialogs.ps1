$ErrorActionPreference = 'Stop'

if (-not (Get-Process -Name acad -ErrorAction SilentlyContinue)) {
    exit 0
}

$source = @'
using System;
using System.Runtime.InteropServices;

public static class SU2CADRunningObjectTable
{
    [DllImport("oleaut32.dll", PreserveSig = false)]
    [return: MarshalAs(UnmanagedType.Interface)]
    private static extern object GetActiveObject(ref Guid clsid, IntPtr reserved);

    public static object Get(string progId)
    {
        Type type = Type.GetTypeFromProgID(progId, true);
        Guid clsid = type.GUID;
        return GetActiveObject(ref clsid, IntPtr.Zero);
    }
}
'@

Add-Type -TypeDefinition $source

$application = $null
foreach ($progId in @('AutoCAD.Application.25', 'AutoCAD.Application')) {
    try {
        $application = [SU2CADRunningObjectTable]::Get($progId)
        if ($application) { break }
    } catch {
        $application = $null
    }
}

if (-not $application) {
    exit 0
}

$document = $application.ActiveDocument
if ($document) {
    $document.SetVariable('FILEDIA', [int16]1)
    $document.SetVariable('CMDDIA', [int16]1)
}

exit 0
