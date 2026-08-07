using System;
using System.Windows.Forms;
using Velopack;

namespace CorporateChatWebView2;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        // Must run as early as possible for install/update hooks.
        VelopackApp.Build().Run();

        ApplicationConfiguration.Initialize();
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new MainForm());
    }
}
