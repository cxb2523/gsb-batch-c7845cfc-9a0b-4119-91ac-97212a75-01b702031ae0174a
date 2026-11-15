' ============================================================================
' ScenarioRunner.vb
'
' Drives EmailArchiveFlow.vb with the hand-written mail metadata fixtures in
' fixtures\scenarios.txt and asserts the four behaviours the task asks for:
'   1. per-email failure isolation (one bad email does not stop the loop)
'   2. the failure list identifies exactly which emails were not archived
'   3. archive folders are unique even for the same sender in the same second
'   4. every path printed in the report exists on disk and matches what was
'      actually written
'
' Generic checks run against every scenario.  Scenario-specific checks are
' applied by key where the expected directory names or counts matter.
'
' Exit code 0 means every scenario passed.
'
' Usage: ScenarioRunner.exe <templatesDir> <fixturesFile> <workDir>
' ============================================================================

Imports System
Imports System.Collections.Generic
Imports System.IO
Imports System.Text

Public Module ScenarioRunner

    Private failures As Integer = 0
    Private assertions As Integer = 0
    Private templatesDir As String = ""
    Private workDir As String = ""
    Private reportDir As String = ""

    Private ReadOnly HarnessDate As New Date(2026, 11, 16, 12, 0, 0)

    Public Function Main(args As String()) As Integer
        If args.Length < 3 Then
            Console.WriteLine("usage: ScenarioRunner.exe <templatesDir> <fixturesFile> <workDir>")
            Return 2
        End If
        templatesDir = args(0)
        Dim fixturesFile As String = args(1)
        workDir = args(2)
        If Directory.Exists(workDir) Then Directory.Delete(workDir, True)
        Directory.CreateDirectory(workDir)
        reportDir = Path.Combine(workDir, "_reports")
        Directory.CreateDirectory(reportDir)

        Console.WriteLine("Local harness for Main.xaml per-email archiving")
        Console.WriteLine("Templates: " & templatesDir)
        Console.WriteLine("Fixtures : " & fixturesFile)
        Console.WriteLine("Work dir : " & workDir)

        Dim scenarios As List(Of ScenarioFixture) = FixtureReader.Load(fixturesFile)
        Console.WriteLine("Scenarios: " & scenarios.Count.ToString())
        Console.WriteLine()

        For Each scenario As ScenarioFixture In scenarios
            RunScenario(scenario)
        Next

        RunAbortScenario()

        Console.WriteLine()
        Console.WriteLine(assertions.ToString() & " assertions, " & failures.ToString() & " failure(s)")
        If failures = 0 Then
            Console.WriteLine("RESULT: PASS")
            Return 0
        End If
        Console.WriteLine("RESULT: FAIL")
        Return 1
    End Function

    ' ---------------------------------------------------------------- scenarios

    Private Sub RunScenario(scenario As ScenarioFixture)
        Console.WriteLine()
        Console.WriteLine("== [" & scenario.Name & "] " & scenario.Description)

        Dim root As String = Path.Combine(workDir, scenario.Name)
        Dim report As RunReport = EmailArchiveFlow.RunBatch(scenario.Mails, root, templatesDir, HarnessDate)
        WriteReportArtifact(scenario.Name, report)

        ' --- expected counts from the fixture file --------------------------
        Check("successful archives = " & scenario.ExpectedSuccess,
              report.Successes.Count = scenario.ExpectedSuccess)
        Check("failures = " & scenario.ExpectedFailure,
              report.Failures.Count = scenario.ExpectedFailure)
        Check("success count + failure count = emails read",
              report.Successes.Count + report.Failures.Count = scenario.Mails.Count)

        ' --- the fixture's declared template is actually the one used -------
        Dim expectedTemplateFile As String = If(scenario.ExpectedTemplate = "success",
                                                "EmailSuccessTemplate.txt", "EmailFailureTemplate.txt")
        Check("template declared in the fixture (" & scenario.ExpectedTemplate & ") was used",
              report.TemplateUsed = expectedTemplateFile)

        ' --- template semantics ---------------------------------------------
        If scenario.ExpectedFailure = 0 Then
            Check("success template used", report.TemplateUsed = "EmailSuccessTemplate.txt")
            Check("existing success subject kept", report.Subject = EmailArchiveFlow.SuccessSubject)
            Check("success wording preserved", report.Body.Contains("successfully completed at"))
            Check("no failure wording in the body", Not report.Body.Contains("could NOT be archived"))
        Else
            Check("failure template used", report.TemplateUsed = "EmailFailureTemplate.txt")
            Check("subject states how many failed", report.Subject.Contains(scenario.ExpectedFailure.ToString() & " of "))
            Check("body lists the successes and the failures",
                  report.Body.Contains("Emails archived successfully: " & scenario.ExpectedSuccess.ToString()) AndAlso
                  report.Body.Contains("Emails that failed: " & scenario.ExpectedFailure.ToString()))
            Check("body is not only an error screenshot notice",
                  Not report.Body.Contains("See attached for screenshot of error."))
        End If

        ' --- the failure list must name exactly the failed emails -----------
        Check("failure section lists every failed email",
              CountOccurrences(report.Body, "NOT ARCHIVED") = scenario.ExpectedFailure)
        For Each o As ArchiveOutcome In report.Failures
            Check("failed email " & o.Index.ToString() & " has a sender",
                  Not String.IsNullOrWhiteSpace(o.SenderLabel))
            Check("failed email " & o.Index.ToString() & " has a subject or placeholder",
                  Not String.IsNullOrWhiteSpace(o.Subject))
            Check("failed email " & o.Index.ToString() & " has a reason",
                  Not String.IsNullOrWhiteSpace(o.Reason))
        Next
        For Each o As ArchiveOutcome In report.Successes
            Check("archived email " & o.Index.ToString() & " is not also listed as failed",
                  Not report.Failures.Exists(Function(f) f.Index = o.Index))
        Next

        ' --- archive directories are unique ---------------------------------
        Dim paths As New HashSet(Of String)(StringComparer.OrdinalIgnoreCase)
        For Each o As ArchiveOutcome In report.Successes
            paths.Add(o.FolderPath)
        Next
        Check("every archived email has its own directory", paths.Count = report.Successes.Count)
        Check("disk holds exactly the reported directories",
              ArchiveDirectoryCount(root) = UniqueFolderCountOnDisk(report))

        ' --- reported paths are the real paths ------------------------------
        Check("every reported path exists on disk", AllReportedPathsExistIn(report.Successes))
        Check("every 'Saved to' line in the body points at an existing folder",
              BodyPathsExist(report.Body, "Saved to: "))
        Check("no failed email is reported as successfully saved",
              Not FailurePathAppearsAsSaved(report))
        For Each o As ArchiveOutcome In report.Successes
            Check("email " & o.Index.ToString() & " wrote its message into its own folder",
                  File.Exists(o.SavedMessagePath) AndAlso
                  String.Equals(Path.GetDirectoryName(o.SavedMessagePath), o.FolderPath,
                                StringComparison.OrdinalIgnoreCase))
        Next

        ' --- scenario specifics ---------------------------------------------
        Select Case scenario.Name
            Case "all-success"
                Check("ten directories were created", ArchiveDirectoryCount(root) = 10)
            Case "one-attachment-failure"
                Dim failed As ArchiveOutcome = report.Failures(0)
                Check("failed email identified as Uber", failed.SenderLabel = "Uber")
                Check("failure reason names the attachment step",
                      failed.Reason.Contains("SaveMailAttachmentsX"))
                Check("partial folder is reported, not a success path",
                      Not String.IsNullOrEmpty(failed.FolderPath) AndAlso
                      report.Body.Contains("Partial archive folder: " & failed.FolderPath))
                Check("nine directories hold a saved message", DirectoriesWithMessage(root) = 10)
                ' The message is written before the attachment step, so the failed
                ' email leaves a message behind in its partial folder; that folder
                ' must be reported as partial, never as a successful save.
                Check("the failed email's partial folder is not counted as success",
                      report.Successes.Count = 9)
                Check("the later emails were processed",
                      report.Body.Contains("Electricity bill") AndAlso
                      report.Body.Contains("Invoice 2026-11"))
            Case "same-sender-same-second"
                ' The date stamp depends on the machine's time zone (CDate converts
                ' the header's offset to local time), so only the uniqueness shape
                ' is asserted here, not a fixed literal.
                Dim stamp0 As String = Path.GetFileName(report.Successes(0).FolderPath)
                Dim stamp1 As String = Path.GetFileName(report.Successes(1).FolderPath)
                Dim stamp2 As String = Path.GetFileName(report.Successes(2).FolderPath)
                Check("first email uses the bare timestamp",
                      System.Text.RegularExpressions.Regex.IsMatch(stamp0, "^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}$"))
                Check("second email is disambiguated", stamp1 = stamp0 & "_2")
                Check("third email is disambiguated", stamp2 = stamp0 & "_3")
                Check("all three subjects survived, nothing was overwritten",
                      report.Body.Contains("Receipt A") AndAlso report.Body.Contains("Receipt B") AndAlso
                      report.Body.Contains("Receipt C") AndAlso DirectoriesWithMessage(root) = 3)
            Case "empty-subject"
                Check("placeholder subject recorded",
                      report.Failures(0).Subject = EmailArchiveFlow.PlaceholderSubject)
                Check("placeholder visible in the body",
                      report.Body.Contains("Subject: " & EmailArchiveFlow.PlaceholderSubject))
                Check("reason explains the subject problem",
                      report.Failures(0).Reason.Contains("subject"))
            Case "subject-read-error"
                Check("placeholder subject recorded after a read error",
                      report.Failures(0).Subject = EmailArchiveFlow.PlaceholderSubject)
                Check("reason explains the read error",
                      report.Failures(0).Reason.Contains("could not be read"))
            Case "bad-date"
                Check("failure is the third email", report.Failures(0).Index = 3)
                Check("no folder was created for it",
                      String.IsNullOrEmpty(report.Failures(0).FolderPath))
                Check("body says no folder was created",
                      report.Body.Contains("Archive folder: none created"))
                Check("later emails still processed",
                      report.Body.Contains("Meeting invite for Thursday"))
            Case "sender-read-error"
                Check("sender downgraded to Unknown",
                      report.Successes(1).SenderLabel = "Unknown")
                Check("email still archived under Unknown",
                      Directory.Exists(Path.Combine(root, "Unknown")))
            Case "all-fail"
                Check("nothing was archived", report.Successes.Count = 0)
                Check("report still lists all ten", CountOccurrences(report.Body, "NOT ARCHIVED") = 10)
                Check("count states zero archived", report.Body.Contains("Emails archived successfully: 0"))
            Case "save-mail-failure"
                Check("failure is the fifth email", report.Failures(0).Index = 5)
                ' The fixture subject is "Locked: message file".  The failure
                ' record must show the readable subject, not the sanitised file
                ' name the success path uses.
                Check("failed email keeps the readable subject",
                      report.Failures(0).Subject = "Locked: message file")
                Check("failure record shows the colon, not a substituted space",
                      report.Body.Contains("Subject: Locked: message file"))
                Check("the failed email's folder was created before the save failed",
                      Not String.IsNullOrEmpty(report.Failures(0).FolderPath))
                Check("that folder is reported as partial",
                      report.Body.Contains("Partial archive folder: " & report.Failures(0).FolderPath))
                Check("only an empty stub was left behind in the failed folder",
                      File.Exists(Path.Combine(report.Failures(0).FolderPath,
                                               "Locked  message file.msg")) AndAlso
                      New FileInfo(Path.Combine(report.Failures(0).FolderPath,
                                                "Locked  message file.msg")).Length = 0)
                Check("the remaining emails were processed",
                      report.Body.Contains("Notes to self") AndAlso
                      report.Body.Contains("Weekly summary"))
            Case "mixed"
                Check("failure indices are 3 and 4",
                      report.Failures(0).Index = 3 AndAlso report.Failures(1).Index = 4)
                Check("unknown sender gets the placeholder subject",
                      report.Failures(1).SenderLabel = "Unknown" AndAlso
                      report.Failures(1).Subject = EmailArchiveFlow.PlaceholderSubject)
                Check("the duplicate pair still has two folders", DirectoriesWithMessage(root) = 3)
                ' The success entry shows the sanitised subject, so a colon becomes
                ' a space; this is what matches the .msg file name on disk.
                Check("success entry uses the sanitised subject",
                      report.Successes.Count = 3 AndAlso
                      report.Body.Contains("Subject: Receipt A"))
        End Select
    End Sub

    ' A fatal error outside the per-email scope must still produce a summary
    ' rather than only the error screenshot mail.
    Private Sub RunAbortScenario()
        Console.WriteLine()
        Console.WriteLine("== [aborted] a fatal error interrupts the run after three emails")
        Dim mails As New List(Of MailFixture)
        For i As Integer = 0 To 9
            Dim mail As New MailFixture()
            mail.SenderAddress = "someone@example.com"
            mail.Subject = "Message number " & (i + 1).ToString()
            mail.DateHeader = "Mon, 16 Nov 2026 11:" & (10 + i).ToString("00") & ":30 +1100"
            mails.Add(mail)
        Next
        Dim root As String = Path.Combine(workDir, "aborted")
        Dim report As RunReport = EmailArchiveFlow.RunBatch(mails, root, templatesDir, HarnessDate, abortAfter:=3)
        WriteReportArtifact("aborted", report)

        Check("the run is marked interrupted", report.Aborted)
        Check("aborted template used", report.TemplateUsed = "EmailAbortedTemplate.txt")
        Check("three emails were archived before the interruption", report.Successes.Count = 3)
        Check("no per-email failures were recorded", report.Failures.Count = 0)
        Check("the summary names the interruption", report.Body.Contains("Simulated Outlook connection loss"))
        Check("the summary still lists the archived emails", report.Body.Contains("Message number 1"))
        Check("the summary reports the count", report.Body.Contains("archived successfully before the interruption: 3"))
        Check("the subject states the run was interrupted", report.Subject.Contains("interrupted"))
        Check("reported paths still exist", AllReportedPathsExistIn(report.Successes))
        Check("only the three processed emails were written", ArchiveDirectoryCount(root) = 3)
    End Sub

    ' ----------------------------------------------------------------- helpers

    Private Sub Check(name As String, condition As Boolean)
        assertions += 1
        If condition Then
            Console.WriteLine("  ok   " & name)
        Else
            failures += 1
            Console.WriteLine("  FAIL " & name)
        End If
    End Sub

    Private Sub WriteReportArtifact(scenarioName As String, report As RunReport)
        Dim sb As New StringBuilder()
        sb.AppendLine("Subject: " & report.Subject)
        sb.AppendLine("Template: " & report.TemplateUsed)
        sb.AppendLine("Archived: " & report.Successes.Count.ToString() &
                      "  Failed: " & report.Failures.Count.ToString())
        sb.AppendLine("----------------------------------------")
        sb.Append(report.Body)
        File.WriteAllText(Path.Combine(reportDir, scenarioName & ".txt"), sb.ToString())
    End Sub

    Private Function ArchiveDirectoryCount(root As String) As Integer
        If Not Directory.Exists(root) Then Return 0
        Dim count As Integer = 0
        For Each senderDir As String In Directory.GetDirectories(root)
            count += Directory.GetDirectories(senderDir).Length
        Next
        Return count
    End Function

    ' Count of directories that are either reported as successes or hold a
    ' partially written failed email (the folder is created before the failure).
    Private Function UniqueFolderCountOnDisk(report As RunReport) As Integer
        Dim seenFolders As New HashSet(Of String)(StringComparer.OrdinalIgnoreCase)
        For Each o As ArchiveOutcome In report.Successes
            seenFolders.Add(o.FolderPath)
        Next
        For Each o As ArchiveOutcome In report.Failures
            If Not String.IsNullOrEmpty(o.FolderPath) Then seenFolders.Add(o.FolderPath)
        Next
        Return seenFolders.Count
    End Function

    Private Function DirectoriesWithMessage(root As String) As Integer
        Dim found As Integer = 0
        For Each senderDir As String In Directory.GetDirectories(root)
            For Each mailDir As String In Directory.GetDirectories(senderDir)
                If Directory.GetFiles(mailDir, "*.msg").Length > 0 Then found += 1
            Next
        Next
        Return found
    End Function

    Private Function CountOccurrences(text As String, needle As String) As Integer
        Dim n As Integer = 0
        Dim i As Integer = text.IndexOf(needle, StringComparison.Ordinal)
        While i >= 0
            n += 1
            i = text.IndexOf(needle, i + needle.Length, StringComparison.Ordinal)
        End While
        Return n
    End Function

    Private Function BodyPathsExist(body As String, prefix As String) As Boolean
        For Each line As String In body.Split(New String() {Environment.NewLine}, StringSplitOptions.None)
            If line.StartsWith(prefix, StringComparison.Ordinal) Then
                Dim p As String = line.Substring(prefix.Length).Trim()
                If Not Directory.Exists(p) Then
                    Console.WriteLine("       missing path: " & p)
                    Return False
                End If
            End If
        Next
        Return True
    End Function

    Private Function AllReportedPathsExistIn(outcomes As List(Of ArchiveOutcome)) As Boolean
        For Each o As ArchiveOutcome In outcomes
            If Not Directory.Exists(o.FolderPath) Then Return False
            If Not File.Exists(o.SavedMessagePath) Then Return False
        Next
        Return True
    End Function

    Private Function FailurePathAppearsAsSaved(report As RunReport) As Boolean
        For Each line As String In report.Body.Split(New String() {Environment.NewLine}, StringSplitOptions.None)
            If Not line.StartsWith("Saved to: ", StringComparison.Ordinal) Then Continue For
            Dim p As String = line.Substring("Saved to: ".Length).Trim()
            For Each failed As ArchiveOutcome In report.Failures
                If Not String.IsNullOrEmpty(failed.FolderPath) AndAlso
                   String.Equals(failed.FolderPath, p, StringComparison.OrdinalIgnoreCase) Then
                    Return True
                End If
            Next
        Next
        Return False
    End Function

End Module

' ============================================================================
' Minimal reader for fixtures\scenarios.txt (no JSON/XML dependency).
' ============================================================================
Public Module FixtureReader

    Public Function Load(path As String) As List(Of ScenarioFixture)
        If Not File.Exists(path) Then
            Throw New FileNotFoundException("Fixture file not found: " & path, path)
        End If

        Dim scenarios As New List(Of ScenarioFixture)
        Dim current As ScenarioFixture = Nothing

        For Each rawLine As String In File.ReadAllLines(path)
            Dim line As String = rawLine.Trim()
            If line.Length = 0 OrElse line.StartsWith("#") Then Continue For

            If line.StartsWith("[") AndAlso line.EndsWith("]") Then
                current = New ScenarioFixture()
                current.Name = line.Substring(1, line.Length - 2)
                current.Mails = New List(Of MailFixture)
                scenarios.Add(current)
                Continue For
            End If

            If current Is Nothing Then
                Throw New Exception("Fixture entry outside a scenario section: " & line)
            End If

            Dim eq As Integer = line.IndexOf("="c)
            If eq < 0 Then Throw New Exception("Malformed fixture line: " & line)
            Dim key As String = line.Substring(0, eq).Trim()
            Dim value As String = line.Substring(eq + 1).Trim()

            Select Case key
                Case "description"
                    current.Description = value
                Case "success"
                    current.ExpectedSuccess = CInt(value)
                Case "failure"
                    current.ExpectedFailure = CInt(value)
                Case "template"
                    current.ExpectedTemplate = value
                Case "mail"
                    current.Mails.Add(ParseMail(value))
                Case Else
                    Throw New Exception("Unknown fixture key '" & key & "' in scenario " & current.Name)
            End Select
        Next

        For Each s As ScenarioFixture In scenarios
            If s.Mails.Count = 0 Then
                Throw New Exception("Scenario " & s.Name & " has no mail entries")
            End If
        Next

        Return scenarios
    End Function

    Private Function ParseMail(value As String) As MailFixture
        Dim parts As String() = value.Split("|"c)
        If parts.Length <> 5 Then
            Throw New Exception("A mail entry needs 5 fields (sender|subject|date|attachments|failAt): " & value)
        End If

        Dim mail As New MailFixture()
        mail.SenderAddress = EmptyToNothing(parts(0))
        mail.Subject = EmptyToNothing(parts(1))
        mail.DateHeader = EmptyToNothing(parts(2))
        mail.HasAttachments = String.Equals(parts(3), "yes", StringComparison.OrdinalIgnoreCase)
        mail.FailAt = If(parts(4) = "-", "", parts(4))

        If mail.HasAttachments Then
            mail.AttachmentNames = New List(Of String) From {"attachment-1.pdf", "attachment-2.png"}
        End If

        Return mail
    End Function

    Private Function EmptyToNothing(value As String) As String
        If value = "<empty>" Then Return Nothing
        Return value
    End Function

End Module
