$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class AmplifaiProfileProcess {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX;
        public int dwY;
        public int dwXSize;
        public int dwYSize;
        public int dwXCountChars;
        public int dwYCountChars;
        public int dwFillAttribute;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION {
        public IntPtr hProcess;
        public IntPtr hThread;
        public int dwProcessId;
        public int dwThreadId;
    }

    [DllImport("advapi32.dll", EntryPoint = "CreateProcessWithLogonW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcessWithLogonW(
        string username, string domain, string password, int logonFlags,
        string applicationName, StringBuilder commandLine, uint creationFlags,
        IntPtr environment, string currentDirectory, ref STARTUPINFO startupInfo,
        out PROCESS_INFORMATION processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeProcess(IntPtr handle, out uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr handle, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static int Run(string username, string password, string application, string arguments, string currentDirectory) {
        var startup = new STARTUPINFO();
        startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
        PROCESS_INFORMATION process;
        // NULL environment lets Windows create the specified user's profiled environment.
        var command = new StringBuilder("\"" + application + "\" " + arguments);
        if (!CreateProcessWithLogonW(username, ".", password, 1, application, command,
                0x08000000, IntPtr.Zero, currentDirectory, ref startup, out process)) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        try {
            uint wait = WaitForSingleObject(process.hProcess, 120000);
            if (wait == 0x102) {
                TerminateProcess(process.hProcess, 1);
                WaitForSingleObject(process.hProcess, 5000);
                throw new TimeoutException("Disposable process exceeded the two-minute bound");
            }
            if (wait != 0) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
            uint result;
            if (!GetExitCodeProcess(process.hProcess, out result)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return unchecked((int)result);
        }
        finally {
            CloseHandle(process.hThread);
            CloseHandle(process.hProcess);
        }
    }
}
'@

function Invoke-ProfiledProcess {
  param(
    [Parameter(Mandatory=$true)][string]$Username,
    [Parameter(Mandatory=$true)][securestring]$Password,
    [Parameter(Mandatory=$true)][string]$FilePath,
    [Parameter(Mandatory=$true)][string]$Arguments,
    [Parameter(Mandatory=$true)][string]$WorkingDirectory
  )
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    return [AmplifaiProfileProcess]::Run($Username, $plain, $FilePath, $Arguments, $WorkingDirectory)
  }
  finally {
    $plain = $null
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
}
