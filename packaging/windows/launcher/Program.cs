using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Text.RegularExpressions;
#if !CLI_LAUNCHER
using System.Windows.Forms;
#endif

namespace AutoSpeechJournal.Launcher
{
    [DataContract]
    internal sealed class CurrentManifest
    {
        [DataMember(Name = "schema_version")]
        public int SchemaVersion { get; set; }

        [DataMember(Name = "version")]
        public string Version { get; set; }

        [DataMember(Name = "targets")]
        public LauncherTargets Targets { get; set; }
    }

    [DataContract]
    internal sealed class LauncherTargets
    {
        [DataMember(Name = "gui")]
        public string Gui { get; set; }

        [DataMember(Name = "cli")]
        public string Cli { get; set; }
    }

    internal static class Program
    {
        private const int ManifestSchemaVersion = 1;
        private const string GuiTarget = "AutoSpeechJournal.exe";
        private const string CliTarget = "AutoSpeechJournal.CLI.exe";

        private static readonly Regex VersionPattern = new Regex(
            @"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$",
            RegexOptions.CultureInvariant);

#if CLI_LAUNCHER
        private const string ExpectedTarget = CliTarget;
#else
        private const string ExpectedTarget = GuiTarget;
#endif

        [STAThread]
        private static int Main()
        {
            try
            {
                string launcherPath = Process.GetCurrentProcess().MainModule.FileName;
                string applicationRoot = Path.GetDirectoryName(launcherPath);
                string targetPath = ResolveTarget(applicationRoot);
                string[] allArguments = Environment.GetCommandLineArgs();
                string arguments = JoinArguments(allArguments, 1);

                ProcessStartInfo startInfo = new ProcessStartInfo
                {
                    FileName = targetPath,
                    Arguments = arguments,
                    WorkingDirectory = Environment.CurrentDirectory,
                    UseShellExecute = false
                };
                Process child = Process.Start(startInfo);
                if (child == null)
                {
                    throw new InvalidOperationException("Windows did not start the selected version.");
                }
#if CLI_LAUNCHER
                child.WaitForExit();
                return child.ExitCode;
#else
                return 0;
#endif
            }
            catch (Exception error)
            {
                ReportError(error.Message);
                return 2;
            }
        }

        internal static string ResolveTarget(string applicationRoot)
        {
            if (String.IsNullOrWhiteSpace(applicationRoot))
            {
                throw new InvalidOperationException("Cannot determine the launcher directory.");
            }

            string manifestPath = Path.Combine(applicationRoot, "current.json");
            string previousManifestPath = Path.Combine(applicationRoot, "current.previous.json");
            Exception currentError = null;
            try
            {
                return ResolveManifestTarget(applicationRoot, manifestPath);
            }
            catch (Exception error)
            {
                currentError = error;
            }

            try
            {
                return ResolveManifestTarget(applicationRoot, previousManifestPath);
            }
            catch (Exception previousError)
            {
                throw new InvalidDataException(
                    "Neither current.json nor current.previous.json selects a usable version. " +
                    "Run Repair from the Start menu or reinstall the application. " +
                    "Current: " + currentError.Message + " Previous: " + previousError.Message,
                    previousError);
            }
        }

        private static string ResolveManifestTarget(string applicationRoot, string manifestPath)
        {
            if (!File.Exists(manifestPath))
            {
                throw new FileNotFoundException("Version manifest is missing.", manifestPath);
            }

            CurrentManifest manifest;
            try
            {
                using (FileStream stream = new FileStream(
                    manifestPath, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    DataContractJsonSerializer serializer =
                        new DataContractJsonSerializer(typeof(CurrentManifest));
                    manifest = serializer.ReadObject(stream) as CurrentManifest;
                }
            }
            catch (Exception error)
            {
                throw new InvalidDataException(
                    "current.json is not valid UTF-8 JSON. Run Repair or reinstall. " + error.Message,
                    error);
            }

            if (manifest == null || manifest.SchemaVersion != ManifestSchemaVersion)
            {
                throw new InvalidDataException("current.json uses an unsupported schema.");
            }
            if (String.IsNullOrWhiteSpace(manifest.Version) ||
                !VersionPattern.IsMatch(manifest.Version) ||
                manifest.Version.IndexOf(Path.DirectorySeparatorChar) >= 0 ||
                manifest.Version.IndexOf(Path.AltDirectorySeparatorChar) >= 0)
            {
                throw new InvalidDataException("current.json contains an invalid version.");
            }
            if (manifest.Targets == null ||
                !String.Equals(manifest.Targets.Gui, GuiTarget, StringComparison.Ordinal) ||
                !String.Equals(manifest.Targets.Cli, CliTarget, StringComparison.Ordinal))
            {
                throw new InvalidDataException("current.json contains unexpected launcher targets.");
            }

            string versionsRoot = Path.GetFullPath(Path.Combine(applicationRoot, "versions"));
            string versionRoot = Path.GetFullPath(Path.Combine(versionsRoot, manifest.Version));
            string requiredPrefix = versionsRoot.TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            if (!versionRoot.StartsWith(requiredPrefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("The selected version resolves outside versions\\.");
            }

            string targetPath = Path.GetFullPath(Path.Combine(versionRoot, ExpectedTarget));
            string targetPrefix = versionRoot.TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            if (!targetPath.StartsWith(targetPrefix, StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(targetPath))
            {
                throw new FileNotFoundException(
                    "The selected Auto Speech Journal version is incomplete. Run Repair or reinstall.",
                    targetPath);
            }
            return targetPath;
        }

        internal static string JoinArguments(string[] arguments, int startIndex)
        {
            StringBuilder commandLine = new StringBuilder();
            for (int index = startIndex; index < arguments.Length; index++)
            {
                if (commandLine.Length > 0)
                {
                    commandLine.Append(' ');
                }
                commandLine.Append(QuoteArgument(arguments[index]));
            }
            return commandLine.ToString();
        }

        // Implements the inverse of CommandLineToArgvW, including empty arguments,
        // embedded quotes, and trailing backslashes before the closing quote.
        internal static string QuoteArgument(string value)
        {
            if (value.Length == 0)
            {
                return "\"\"";
            }
            if (value.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '\"' }) < 0)
            {
                return value;
            }

            StringBuilder quoted = new StringBuilder();
            quoted.Append('\"');
            int backslashes = 0;
            foreach (char character in value)
            {
                if (character == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (character == '\"')
                {
                    quoted.Append('\\', backslashes * 2 + 1);
                    quoted.Append('\"');
                    backslashes = 0;
                    continue;
                }
                quoted.Append('\\', backslashes);
                backslashes = 0;
                quoted.Append(character);
            }
            quoted.Append('\\', backslashes * 2);
            quoted.Append('\"');
            return quoted.ToString();
        }

        private static void ReportError(string message)
        {
            string detail = "Auto Speech Journal could not start.\r\n\r\n" + message;
#if CLI_LAUNCHER
            Console.Error.WriteLine(detail);
#else
            MessageBox.Show(
                detail,
                "Auto Speech Journal",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
#endif
        }
    }
}
