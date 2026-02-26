import textwrap

# a folder where the script will search for the roadmap mail 
FOLDER = 'inbox'

# stop search when i see more than X days old mails 
MAX_AGE_DAYS = 8

# the subject (exact match)
MESSAGE_SUBJECT = 'CONFIDENTIAL - AWS Roadmap Items of Interest - As provided under NDA'

# the text to look for. If this text not found, the roadmap mail will be ignored.
MESSAGE_DISTINQUISHER = 'email for Enterprise Support customers'


# list of clients
MY_CLIENTS = [
    {
        'name': 'Super Client',

        'to': ['the-boss@super-client.com', 'the-bosses-boss@super-client.com',],
        'cc': ['aws-super-team@amazon.com',],
        'bcc': [],

        'subject': '[{client_name}][W{week_number}]: {subject}',
                    # other vars: {date} - iso date 2021-05-30; {date.split('-')[0]} - year

        'header': textwrap.dedent('''

              <p class="MsoNormal">
                <span style="font-size:10.0pt;font-family:&quot;Amazon Ember&quot;,sans-serif;color:black;background:red">
                  Shared under NDA. Please do not forward outside of {client_name}.<br><br>
                </span>
              </p>
              <p>
                <span style="font-size:10.0pt;font-family:&quot;Amazon Ember&quot;,sans-serif">
                  Hello {client_name} Team!
                </span>
              </p>

        '''.strip()),

        # a standalone text will be high-lighted with yellow. Deprecated. Please use highlights.
        'interests': [
            "Amazon EC2",
            "Amazon RDS",
        ],

        # full text fragment containing the key will be high-lighted
        'highlights': {
            "Oracle": "yellow",
            "Korea": "yellow",
        },

    },
]