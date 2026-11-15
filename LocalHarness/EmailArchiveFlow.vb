' ============================================================================
' EmailArchiveFlow.vb
'
' A dependency-free, locally executable mirror of the per-email archiving logic
' in Main.xaml.  It intentionally reproduces the same steps in the same order so
' that the behaviour of the UiPath workflow can be exercised without UiPath
' Studio, without the desktop Outlook application and without the real
' "UiPath Test" folder.
'
' What is mirrored 1:1 with Main.xaml:
'   * sender-address read wrapped in its own try/catch (a bad sender does not
'     fail the email; the label falls back to "Unknown")
'   * the same sender -> label switch table
'   * subject read, empty-subject check, the same character replacements and the
'     same Path.GetInvalidFileNameChars() clean-up
'   * Date header parsing with CDate and the same "yyyy-MM-dd hh-mm-ss" stamp
'   * unique archive folder allocation (base name, then "_2", "_3", ... while
'     the directory exists) and the resolved absolute path used for both the
'     save calls and the report
'   * per-email try/catch that records "From" / "Subject" (placeholder when the
'     subject is unavailable) / "Reason" and then continues the loop
'   * success list, failure list, template selection (success template only
'     when every email was archived, failure template as soon as one failed)
'     and the exact attribute lines used by the report body
'
' What is NOT modelled: see LocalHarness/README.md.
' ============================================================================

Imports System
Imports System.Collections.Generic
Imports System.IO
Imports Microsoft.VisualBasic

Public Class MailFixture
    ' Synthetic mail metadata, the only input the local flow needs.
    Public Property SenderAddress As String = ""
    Public Property Subject As String
    Public Property DateHeader As String = ""
    Public Property HasAttachments As Boolean = False
    Public Property AttachmentNames As List(Of String)
    Public Property FailAt As String = ""
End Class

Public Class ScenarioFixture
    Public Property Name As String = ""
    Public Property Description As String = ""
    Public Property ExpectedSuccess As Integer = 0
    Public Property ExpectedFailure As Integer = 0
    Public Property ExpectedTemplate As String = ""
    Public Property Mails As List(Of MailFixture)
End Class

Public Class ArchiveOutcome
    Public Property Index As Integer = 0
    Public Property Succeeded As Boolean = False
    Public Property SenderLabel As String = "Unknown"
    Public Property SenderAddress As String = ""
    ' Human-readable subject, used for the failure record.  Placeholder when the
    ' subject could not be read or was empty (mirrors varMailSubject).
    Public Property Subject As String = PlaceholderSubject
    ' Sanitised subject used in the success report, set only once the file name
    ' is known to be usable (mirrors varMailSubjectDisplay).
    Public Property ReportSubject As String = ""
    Public Property DateStamp As String = ""
    ' Absolute path actually used on disk.  Empty when the email failed before
    ' a folder was created; a path when the failure happened after creation.
    Public Property FolderPath As String = ""
    Public Property HasAttachments As Boolean = False
    Public Property Reason As String = ""
    Public Property SavedMessagePath As String = ""
End Class

Public Class RunReport
    Public Property Successes As New List(Of ArchiveOutcome)
    Public Property Failures As New List(Of ArchiveOutcome)
    Public Property Body As String = ""
    Public Property Subject As String = ""
    Public Property TemplateUsed As String = ""
    Public Property ArchiveRoot As String = ""
    Public Property GeneratedAt As DateTime
    Public Property Aborted As Boolean = False
    Public Property AbortMessage As String = ""
End Class

Public Module EmailArchiveFlow

    Public Const PlaceholderSubject As String = "(No Subject)"
    Public Const SuccessSubject As String = "Email Automation Report: Your 10 Most Recent Unread Emails"

    ' Mirrors the Switch activity in Main.xaml, including its case-sensitive
    ' string comparison against the raw From address.
    Public Function MapSenderLabel(address As String) As String
        Select Case address
            Case "no-reply@m.mail.coursera.org", "Coursera@email.coursera.org"
                Return "Coursera"
            Case "online@e.bigw.com.au", "mail@bigw.com.au"
                Return "BIG W"
            Case "ubereats@uber.com", "uberone@uber.com"
                Return "Uber"
            Case "noreplytickets@taronga.org.au"
                Return "Taronga Zoo"
            Case "ebills@winconnect.com.au"
                Return "WinConnect Electricity"
            Case "meenoxiong@gmail.com"
                Return "Me"
            Case Else
                Return "Unknown"
        End Select
    End Function

    Public Function SanitiseSubject(raw As String) As String
        Dim cleaned As String = raw.Replace("'", " ").Replace(":", " ").Replace(".", " ").
            Replace("【", " ").Replace("】", " ").Replace("+", " ").Replace("?", " ").Replace("!", " ").Trim()
        Return String.Join(" ", cleaned.Split(Path.GetInvalidFileNameChars())).Trim()
    End Function

    ''' <summary>
    ''' Archives one email.  State is written into <paramref name="outcome"/> as
    ''' it is discovered, so a thrown exception still leaves the caller with the
    ''' sender/subject/folder information collected up to the failing step --
    ''' this is exactly how the Main.xaml outer-scope variables behave.
    ''' </summary>
    Public Sub ArchiveOne(outcome As ArchiveOutcome, fixture As MailFixture, archiveRoot As String)
        ' --- sender ---------------------------------------------------------
        ' Main.xaml wraps the sender read in its own TryCatch: a broken sender
        ' is logged and downgraded, it does not fail the email.
        If fixture.FailAt = "sender-read" Then
            Console.WriteLine("  [warn] could not read the sender address, using Unknown")
        Else
            If fixture.SenderAddress IsNot Nothing Then
                outcome.SenderAddress = fixture.SenderAddress
            End If
        End If
        outcome.SenderLabel = MapSenderLabel(If(outcome.SenderAddress, ""))

        ' --- subject --------------------------------------------------------
        If fixture.FailAt = "subject-read" Then
            Throw New Exception("The email subject could not be read: simulated Outlook error.")
        End If
        If String.IsNullOrWhiteSpace(fixture.Subject) Then
            Throw New Exception("The email subject is empty or unavailable, so the email cannot be archived.")
        End If
        ' The readable subject is kept first (varMailSubject) and survives into the
        ' failure record; the sanitised copy becomes the file name
        ' (varMailSubjectFile) and only after it is known to be usable does it
        ' also become the subject used in the success report
        ' (varMailSubjectDisplay) -- the same variable progression as Main.xaml.
        outcome.Subject = fixture.Subject.Trim()
        Dim fileName As String = SanitiseSubject(fixture.Subject)
        If String.IsNullOrWhiteSpace(fileName) Then
            Throw New Exception("The email subject is empty or contains no usable character, so the email cannot be archived.")
        End If
        outcome.ReportSubject = fileName

        ' --- Date header ----------------------------------------------------
        If fixture.FailAt = "date-parse" Then
            Throw New Exception("The Date header could not be parsed: simulated malformed header.")
        End If
        Dim parsed As Date = CDate(fixture.DateHeader)
        outcome.DateStamp = parsed.ToString("yyyy-MM-dd hh-mm-ss")

        ' --- unique archive folder -----------------------------------------
        Dim baseFolder As String = Path.Combine(archiveRoot, outcome.SenderLabel, outcome.DateStamp)
        Dim folder As String = baseFolder
        Dim suffix As Integer = 1
        While Directory.Exists(folder)
            suffix += 1
            folder = baseFolder & "_" & suffix.ToString()
        End While
        outcome.FolderPath = Directory.CreateDirectory(folder).FullName

        ' --- save the message ----------------------------------------------
        Dim messagePath As String = Path.Combine(outcome.FolderPath, fileName & ".msg")
        ' Record the intended path before writing, exactly like the workflow's
        ' variables being visible to the catch block when SaveMailX throws.
        outcome.SavedMessagePath = messagePath
        If fixture.FailAt = "save-mail" Then
            ' Mirrors SaveMailX failing part-way: the .msg is created but left
            ' unwritten, so the folder exists without a usable message file.
            File.WriteAllText(messagePath, "")
            Throw New Exception("Simulated SaveMailX failure: the message file could not be written.")
        End If
        File.WriteAllText(messagePath, "Subject: " & fixture.Subject & Environment.NewLine &
                          "From: " & outcome.SenderAddress & Environment.NewLine &
                          "Date: " & fixture.DateHeader & Environment.NewLine)

        ' --- save attachments ----------------------------------------------
        Dim attachmentFolder As String = Path.Combine(outcome.FolderPath, "Attachments")
        If fixture.FailAt = "save-attachments" Then
            ' Mirrors SaveMailAttachmentsX having created the folder before it
            ' failed, leaving a partially written directory behind.
            Directory.CreateDirectory(attachmentFolder)
            File.WriteAllText(Path.Combine(attachmentFolder, "partial.bin"), "")
            Throw New Exception("Simulated SaveMailAttachmentsX failure: access denied while writing an attachment.")
        End If
        If fixture.HasAttachments Then
            Directory.CreateDirectory(attachmentFolder)
            Dim names As List(Of String) = fixture.AttachmentNames
            If names Is Nothing OrElse names.Count = 0 Then
                names = New List(Of String) From {"attachment.bin"}
            End If
            For Each name As String In names
                File.WriteAllText(Path.Combine(attachmentFolder, name), "simulated attachment")
            Next
        End If

        ' Same check as the If activity after SaveMailAttachmentsX.
        outcome.HasAttachments = Directory.Exists(attachmentFolder) AndAlso
                                 Directory.GetFiles(attachmentFolder).Length > 0
        outcome.Succeeded = True
        Console.WriteLine("  [info] saved """ & outcome.ReportSubject & """ to " & outcome.FolderPath)
    End Sub

    ''' <summary>
    ''' Runs the whole batch.  Every email is isolated in its own Try/Catch;
    ''' one failure never stops the loop and never stops the final report.
    ''' <paramref name="abortAfter"/> simulates a fatal error raised outside the
    ''' per-email protection (for example Outlook losing its connection), which
    ''' mirrors the outer TryCatch in Main.xaml.
    ''' </summary>
    Public Function RunBatch(fixtures As List(Of MailFixture), archiveRoot As String,
                             templatesDir As String, generatedAt As DateTime,
                             Optional abortAfter As Integer = -1) As RunReport
        Dim report As New RunReport()
        report.ArchiveRoot = archiveRoot
        report.GeneratedAt = generatedAt
        Directory.CreateDirectory(archiveRoot)

        Dim successLines As New List(Of String)
        Dim failureLines As New List(Of String)
        Dim counter As Integer = 1

        Try
            For Each fixture As MailFixture In fixtures
                If abortAfter >= 0 AndAlso counter > abortAfter Then
                    Throw New Exception("Simulated Outlook connection loss outside the per-email scope.")
                End If

                ' Fresh per-email state, exactly like the reset Assigns in Main.xaml.
                Dim outcome As New ArchiveOutcome()
                outcome.Index = counter
                Try
                    ArchiveOne(outcome, fixture, archiveRoot)
                    successLines.Add(BuildSuccessLine(outcome))
                    report.Successes.Add(outcome)
                Catch ex As Exception
                    outcome.Succeeded = False
                    outcome.Reason = ex.Message
                    failureLines.Add(BuildFailureLine(outcome))
                    report.Failures.Add(outcome)
                    Console.WriteLine("  [error] email " & counter & " from " & outcome.SenderLabel &
                                      " was not archived: " & ex.Message)
                End Try
                counter += 1
            Next
        Catch fatal As Exception
            ' The outer Catch in Main.xaml records the reason, then the Finally
            ' block still sends a summary that says the run stopped early.
            report.Aborted = True
            report.AbortMessage = fatal.Message
            Console.WriteLine("  [fatal] run interrupted: " & fatal.Message)
        End Try

        ' Report assembly: same templates, same placeholder indices, same
        ' template choice as the Finally block in Main.xaml.
        Dim successText As String = JoinLines(successLines)
        Dim failureText As String = JoinLines(failureLines)
        Dim successTemplatePath As String = Path.Combine(templatesDir, "EmailSuccessTemplate.txt")
        Dim failureTemplatePath As String = Path.Combine(templatesDir, "EmailFailureTemplate.txt")
        Dim abortedTemplatePath As String = Path.Combine(templatesDir, "EmailAbortedTemplate.txt")

        If report.Aborted Then
            report.TemplateUsed = "EmailAbortedTemplate.txt"
            report.Body = String.Format(File.ReadAllText(abortedTemplatePath), generatedAt,
                                        report.AbortMessage, report.Successes.Count,
                                        report.Failures.Count, successText, failureText)
            report.Subject = "Email Automation Report: interrupted after " &
                             report.Successes.Count.ToString() & " archived, " &
                             report.Failures.Count.ToString() & " failed"
        ElseIf report.Failures.Count > 0 Then
            report.TemplateUsed = "EmailFailureTemplate.txt"
            report.Body = String.Format(File.ReadAllText(failureTemplatePath), generatedAt,
                                        report.Successes.Count, report.Failures.Count,
                                        successText, failureText)
            report.Subject = "Email Automation Report: " & report.Failures.Count.ToString() &
                             " of " & (report.Successes.Count + report.Failures.Count).ToString() &
                             " emails could not be archived"
        Else
            report.TemplateUsed = "EmailSuccessTemplate.txt"
            report.Body = String.Format(File.ReadAllText(successTemplatePath), generatedAt, successText)
            report.Subject = SuccessSubject
        End If

        Return report
    End Function

    ' These two functions mirror the AddToCollection Item expressions verbatim.
    ' The success line uses the sanitised display subject; the failure line uses
    ' the readable one, which is why a failure after the sanitising step still
    ' shows the subject the user recognises.
    Public Function BuildSuccessLine(o As ArchiveOutcome) As String
        Return "Email " & o.Index.ToString() & " sent on " & o.DateStamp & Environment.NewLine &
               "Subject: " & o.ReportSubject & Environment.NewLine &
               "From: " & o.SenderLabel & Environment.NewLine &
               "Has Attachments: " & If(o.HasAttachments, "Yes", "No") & Environment.NewLine &
               "Saved to: " & o.FolderPath & Environment.NewLine &
               "----------------------------------------------------------------------------------"
    End Function

    Public Function BuildFailureLine(o As ArchiveOutcome) As String
        Return "Email " & o.Index.ToString() & " NOT ARCHIVED" & Environment.NewLine &
               "From: " & o.SenderLabel &
               If(String.IsNullOrWhiteSpace(o.SenderAddress), "", " <" & o.SenderAddress & ">") & Environment.NewLine &
               "Subject: " & o.Subject & Environment.NewLine &
               "Reason: " & o.Reason & Environment.NewLine &
               If(String.IsNullOrWhiteSpace(o.FolderPath), "Archive folder: none created",
                  "Partial archive folder: " & o.FolderPath) & Environment.NewLine &
               "----------------------------------------------------------------------------------"
    End Function

    Private Function JoinLines(lines As List(Of String)) As String
        If lines.Count = 0 Then Return String.Empty
        Return String.Join(Environment.NewLine, lines.ToArray()) & Environment.NewLine
    End Function

End Module
