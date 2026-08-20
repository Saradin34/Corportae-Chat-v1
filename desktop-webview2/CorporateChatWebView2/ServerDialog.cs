namespace CorporateChatWebView2;

public sealed class ServerDialog : Form
{
    private readonly TextBox _input = new();
    public string Server => _input.Text.Trim();

    public ServerDialog(string current)
    {
        Text = "Сменить сервер";
        Width = 460;
        Height = 170;
        StartPosition = FormStartPosition.CenterParent;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;

        var label = new Label { Text = "Адрес сервера Corporate Chat:", Left = 14, Top = 14, Width = 400 };
        _input.Left = 14;
        _input.Top = 40;
        _input.Width = 410;
        _input.Text = current;

        var ok = new Button { Text = "Сохранить", Left = 238, Width = 90, Top = 78, DialogResult = DialogResult.OK };
        var cancel = new Button { Text = "Отмена", Left = 334, Width = 90, Top = 78, DialogResult = DialogResult.Cancel };

        Controls.Add(label);
        Controls.Add(_input);
        Controls.Add(ok);
        Controls.Add(cancel);
        AcceptButton = ok;
        CancelButton = cancel;
    }
}
